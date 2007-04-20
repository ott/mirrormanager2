from turbogears.identity.exceptions import IdentityFailure
import logging
import cherrypy


import turbogears
from turbogears import controllers, expose, validate, redirect, widgets, validators, error_handler, exception_handler
from turbogears import identity
from tgfastdata import DataController
import sqlobject
from sqlobject.sqlbuilder import *

from mirrors import my_validators
from mirrors.model import *
from mirrors.lib import createErrorString


log = logging.getLogger("mirrors.controllers")

def siteadmin_check(site, identity):
    if not site.is_siteadmin(identity):
        turbogears.flash("Error:You are not an admin for Site %s" % site.name)
        raise redirect("/")

def downstream_siteadmin_check(site, identity):
    if not site.is_siteadmin(identity) and not site.is_downstream_siteadmin(identity):
        turbogears.flash("Error:You are not an admin for Site %s or for a Site immediately downstream from Site %s" % (site.name, site.name))
        raise redirect("/")



# From the TurboGears book
class content:
    @turbogears.expose()
    def default(self, *vpath, **params):
        if len(vpath) == 1:
            identifier = vpath[0]
            action = self.read
            verb = 'read'
        elif len(vpath) == 2:
            identifier, verb = vpath
            verb = verb.replace('.','_')
            action = getattr(self, verb, None)
            if not action:
                raise cherrypy.NotFound
            if not action.exposed:
                raise cherrypy.NotFound
        else:
            raise cherrypy.NotFound


        if verb == "new":
            return action(**params)
        elif verb == "create":
            return action(**params)
        else:
            try:
                item=self.get(identifier)
            except sqlobject.SQLObjectNotFound:
                raise cherrypy.NotFound

            return action(item['values'], **params)




class SiteFields(widgets.WidgetsList):
    licensesAccepted = widgets.CheckBox(label="I agree to the Fedora Legal policies linked above")
    name     = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty), label="Site Name")
    password = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty), label="Site Password", help_text="used by report_mirrors script, you make this anything you want")
    orgUrl   = widgets.TextField(label="Organization URL", validator=validators.Any(validators.All(validators.UnicodeString,validators.URL),validators.Empty), attrs=dict(size='30'), help_text="Company/School/Organization URL e.g. http://www.redhat.com") 
    private  = widgets.CheckBox(help_text="e.g. Not available to the public")
    admin_active = widgets.CheckBox("admin_active",default=True, help_text="Clear to temporarily disable this site.")
    user_active = widgets.CheckBox(default=True, help_text="Clear to temporarily disable this site.")


site_form = widgets.TableForm(fields=SiteFields(),
                              submit_text="Save Site")


class SiteController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def disabled_fields(self, site=None):
        disabled_fields = []
        if not identity.in_group("sysadmin"):
            disabled_fields.append('admin_active')
        if site is not None:
            if not site.is_siteadmin(identity):
                for a in ['password', 'user_active', 'private']:
                    disabled_fields.append(a)
            
        return disabled_fields

    def get(self, id, tg_errors=None, tg_source=None, **kwargs):
        site = Site.get(id)
        return dict(values=site, disabled_fields=self.disabled_fields(site=site))

    @expose(template="mirrors.templates.site")
    def read(self, site):
        downstream_siteadmin_check(site, identity)
        submit_action = turbogears.url("/site/%s/update" % site.id)
        return dict(form=site_form, values=site, action=submit_action, disabled_fields=self.disabled_fields(site=site))

    @expose(template="mirrors.templates.site")
    def new(self, **kwargs):
        submit_action = turbogears.url("/site/0/create")
        return dict(form=site_form, values=None, action=submit_action, disabled_fields=self.disabled_fields())
    
    @expose(template="mirrors.templates.site")
    @validate(form=site_form)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('licensesAccepted') or not kwargs['licensesAccepted']:
            turbogears.flash("Error:You must accept the license agreements to create a Site")
            raise turbogears.redirect("/")
        if not identity.in_group("sysadmin") and kwargs.has_key('admin_active'):
            del kwargs['admin_active']
        kwargs['licensesAcceptedBy'] = identity.current.user_name
        kwargs['createdBy'] = identity.current.user_name
        try:
            site = Site(**kwargs)
            site.accept_licenses(identity)
            SiteAdmin(site=site, username=identity.current.user_name)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error:Site %s already exists" % kwargs['name'])
        turbogears.flash("Site created.")
        raise turbogears.redirect("/site/%s" % site.id)

    @expose(template="mirrors.templates.site")
    @validate(form=site_form)
    @error_handler()
    def update(self, site, tg_errors=None, **kwargs):
        siteadmin_check(site, identity)

        if tg_errors is not None:
            errstr = ""
            for k, v in tg_errors.iteritems():
                errstr += "%s: %s\n" % (k, v)
            turbogears.flash("Error: %s" % errstr)
            return dict(form=site_form, values=site, action = turbogears.url("/site/%s/update" % site.id),
                        disabled_fields=self.disabled_fields())
        
        if kwargs.has_key('licensesAccepted') and kwargs['licensesAccepted']:
            kwargs['licensesAcceptedBy'] = identity.current.user_name
        else:
            turbogears.flash("Error:You must accept the license agreements to update a Site")
            return dict(form=site_form, values=site, action = turbogears.url("/site/%s/update" % site.id),
                        disabled_fields=self.disabled_fields())

        # in case we ever have to reset the licensesAccepted field for everyone
        # we drop it here as we're not letting them uncheck it anyow.
        if kwargs.has_key('licensesAccepted'):
            del kwargs['licensesAccepted']
        if not identity.in_group("sysadmin") and kwargs.has_key('admin_active'):
            del kwargs['admin_active']
        site.set(**kwargs)
        if not site.licensesAccepted:
            site.accept_licenses(identity)
        site.sync()
        turbogears.flash("Site Updated")
        raise turbogears.redirect("/site/%s" % site.id)

    @expose(template="mirrors.templates.site")
    def delete(self, site, **kwargs):
        siteadmin_check(site, identity)
        site.destroySelf()
        raise turbogears.redirect("/")

    @expose(template="mirrors.templates.site")
    def s2s_delete(self, site, **kwargs):
        siteadmin_check(site, identity)
        dsite = Site.get(kwargs['dsite'])
        site.del_downstream_site(dsite)
        raise turbogears.redirect("/site/%s" % site.id)


##############################################
class SiteAdminFields(widgets.WidgetsList):
    username = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty, help_text="FAS username you wish to have be an admin for this site"))


siteadmin_form = widgets.TableForm(fields=SiteAdminFields(),
                              submit_text="Create Site Admin")


class SiteAdminController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def get(self, id):
        v = SiteAdmin.get(id)
        return dict(values=v, site=v.site, title="Site Admin")
    
    @expose(template="mirrors.templates.boringsiteform")
    def new(self, **kwargs):
        siteid=kwargs['siteid']
        try:
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")

        siteadmin_check(site, identity)
        submit_action = turbogears.url("/siteadmin/0/create?siteid=%s" % siteid)
        return dict(form=siteadmin_form, values=None, action=submit_action, title="New Site Admin", site=site)
    
    @expose(template="mirrors.templates.boringsiteform")
    @error_handler(new)
    @validate(form=siteadmin_form)
    def create(self, **kwargs):
        if not kwargs.has_key('siteid'):
            turbogears.flash("Error: form didn't provide siteid")
            raise redirect("/")
        siteid = kwargs['siteid']

        try:
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            turbogears.flash("Error: Site %s does not exist" % siteid)
            raise redirect("/")

        siteadmin_check(site, identity)
        username = kwargs['username']
        try:
            siteadmin = SiteAdmin(site=site, username=username)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error:SiteAdmin %s already exists" % kwargs['username'])
        turbogears.flash("SiteAdmin created.")
        raise turbogears.redirect("/site/%s" % siteid)

    @expose(template="mirrors.templates.boringsiteform")
    def delete(self, siteadmin, **kwargs):
        site = siteadmin.my_site()
        siteadmin_check(site, identity)
        siteadmin.destroySelf()
        raise turbogears.redirect("/site/%s" % site.id)


##############################################
class SiteToSiteFields(widgets.WidgetsList):
    def get_sites_options():
        return [(s.id, s.name) for s in Site.select(orderBy='name')]

    sites = widgets.MultipleSelectField(options=get_sites_options, size=15)
                                        

site_to_site_form = widgets.TableForm(fields=SiteToSiteFields(),
                                      submit_text="Add Downstream Site")


class SiteToSiteController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def get(self, id):
        v = SiteToSite.get(id)
        return dict(values=v, site=v.upstream_site)
    
    @expose(template="mirrors.templates.boringsiteform")
    def new(self, **kwargs):
        siteid=kwargs['siteid']
        try:
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")

        siteadmin_check(site, identity)
        submit_action = turbogears.url("/site2site/0/create?siteid=%s" % siteid)
        return dict(form=site_to_site_form, values=None, action=submit_action, title="Add Downstream Site", site=site)
    
    @expose()
    @validate(form=site_to_site_form)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('siteid'):
            turbogears.flash("Error: form didn't provide siteid")
            raise redirect("/")
        siteid = kwargs['siteid']

        try:
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            turbogears.flash("Error: Site %s does not exist" % siteid)
            raise redirect("/")

        siteadmin_check(site, identity)
        sites = kwargs['sites']
        for dssite in sites:
            if dssite == site.id:
                continue
            try:
                site2site = SiteToSite(upstream_site=site, downstream_site=dssite)
            except: 
                pass
        turbogears.flash("SiteToSite created.")
        raise turbogears.redirect("/site/%s" % siteid)

    @expose()
    def delete(self, site2site, **kwargs):
        site = site2site.my_site()
        siteadmin_check(site, identity)
        site2site.destroySelf()
        raise turbogears.redirect("/site/%s" % site.id)



##############################################
class HostFields(widgets.WidgetsList):
    name = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty), attrs=dict(size='30'), label="Host Name",
                             help_text="Name of server as seen by a public end user")
    admin_active = widgets.CheckBox("admin_active", default=True, help_text="Clear to temporarily disable this host")
    user_active = widgets.CheckBox(default=True, help_text="Clear to temporarily disable this host")
    country = widgets.TextField(validator=validators.Any(validators.Regex(r'^[a-zA-Z][a-zA-Z]$'),validators.Empty),
                                help_text="2-letter ISO country code" )
    private = widgets.CheckBox(help_text="e.g. not available to the public, an internal private mirror")
    robot_email = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.Email), help_text="email address, will receive notice of upstream content updates")
    bandwidth = widgets.TextField(validator=validators.Any(validators.UnicodeString, validators.Empty), help_text="text, how much bandwidth you might be able to offer to a public end user")
    comment = widgets.TextField(validator=validators.Any(validators.UnicodeString, validators.Empty), help_text="text, anything else you'd like a public end user to know about your mirror")


host_form = widgets.TableForm(fields=HostFields(),
                              submit_text="Save Host")

class HostController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def disabled_fields(self, host=None):
        disabled_fields = []
        if not identity.in_group("sysadmin"):
            disabled_fields.append('admin_active')

        if host is not None:
            site = host.my_site()
            if not site.is_siteadmin(identity):
                for a in ['user_active', 'private', 'robot_email']:
                    disabled_fields.append(a)
        return disabled_fields


    def get(self, id):
        host = Host.get(id)
        return dict(values=host)

    @expose(template="mirrors.templates.host")
    def new(self, **kwargs):
        try:
            siteid=kwargs['siteid']
            site = Site.get(siteid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")
        submit_action = turbogears.url("/host/0/create?siteid=%s" % siteid)
        return dict(form=host_form, values=None, action=submit_action, disabled_fields=self.disabled_fields(),
                    title="Create Host", site=Site.get(siteid))

    @expose(template="mirrors.templates.host")
    @validate(form=host_form)
    @error_handler()
    def create(self, **kwargs):
        if not identity.in_group("sysadmin") and kwargs.has_key('admin_active'):
            del kwargs['admin_active']
        site = Site.get(kwargs['siteid'])
        del kwargs['siteid']
        try:
            host = Host(site=site, **kwargs)
            submit_action = turbogears.url("/host/%s/update" % host.id)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error:Host %s already exists" % kwargs['name'])
            submit_action = turbogears.url("/host/0/create?siteid=%s" % site.id)
        
        turbogears.flash("Host created.")
        raise turbogears.redirect("/host/%s" % host.id)


    @expose(template="mirrors.templates.host")
    def read(self, host):
        downstream_siteadmin_check(host.my_site(), identity)
        submit_action = turbogears.url("/host/%s/update" % host.id)
        return dict(form=host_form, values=host, action=submit_action,
                    disabled_fields=self.disabled_fields(host=host), title="Host", site=host.site)

    @expose(template="mirrors.templates.host")
    @validate(form=host_form)
    @error_handler()
    def update(self, host, tg_errors=None, **kwargs):
        siteadmin_check(host.my_site(), identity)

        if tg_errors is not None:
            errstr = ""
            for k, v in tg_errors.iteritems():
                errstr += "%s: %s\n" % (k, v)
            turbogears.flash("Error: %s" % errstr)
            return dict(form=host_form, values=host, action = turbogears.url("/host/%s/update" % host.id),
                        disabled_fields=self.disabled_fields(host=host), title="Host", site=host.site)


        if not identity.in_group("sysadmin") and kwargs.has_key('admin_active'):
            del kwargs['admin_active']
        host.set(**kwargs)
        host.sync()
        turbogears.flash("Host Updated")
        raise turbogears.redirect("/host/%s" % host.id)

    @expose()
    def delete(self, host, **kwargs):
        siteadmin_check(host.my_site(), identity)
        siteid = host.site.id
        host.destroySelf()
        raise turbogears.redirect("/site/%s" % siteid)


##################################################################33
# HostCategory
##################################################################33
class HostCategoryFieldsNew(widgets.WidgetsList):
    def get_category_options():
        return [(c.id, c.name) for c in Category.select(orderBy='name')]
    category = widgets.SingleSelectField(options=get_category_options)
    admin_active = widgets.CheckBox(default=True)
    user_active = widgets.CheckBox(default=True, help_text="Clear to temporarily disable this category")
    upstream = widgets.TextField(validator=validators.Any(validators.UnicodeString,validators.Empty), attrs=dict(size='30'), help_text='e.g. rsync://download.fedora.redhat.com/fedora-linux-core')

class LabelObjName(widgets.Label):
    template = """
    <label xmlns:py="http://purl.org/kid/ns#"
    id="${field_id}"
    class="${field_class}"
    py:if="value is not None"
    py:content="value.name"
    />
    """

class HostCategoryFieldsRead(widgets.WidgetsList):
    category = LabelObjName()
    admin_active = widgets.CheckBox(default=True)
    user_active = widgets.CheckBox(default=True)
    upstream = widgets.TextField(attrs=dict(size='30'), validator=validators.Any(validators.UnicodeString,validators.Empty),
                                 help_text='e.g. rsync://download.fedora.redhat.com/fedora-linux-core')

host_category_form_new = widgets.TableForm(fields=HostCategoryFieldsNew(),
                                       submit_text="Save Host Category")

host_category_form_read = widgets.TableForm(fields=HostCategoryFieldsRead(),
                                            submit_text="Save Host Category")



class HostCategoryController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()

    def disabled_fields(self, host=None):
        disabled_fields = []
        if not identity.in_group("sysadmin"):
            disabled_fields.append('admin_active')
        return disabled_fields

    def get(self, id):
        return dict(values=HostCategory.get(id))

    @expose(template="mirrors.templates.hostcategory")
    def new(self, **kwargs):

        try:
            hostid=kwargs['hostid']
            host = Host.get(hostid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")
        siteadmin_check(host.my_site(), identity)
        submit_action = turbogears.url("/host_category/0/create?hostid=%s" % hostid)
        return dict(form=host_category_form_new, values=None, action=submit_action, disabled_fields=self.disabled_fields(), host=host)
    
    
    @expose(template="mirrors.templates.hostcategory")
    def read(self, hostcategory):
        downstream_siteadmin_check(hostcategory.my_site(), identity)
        submit_action = turbogears.url("/host_category/%s/update" % hostcategory.id)
        disabled_fields=self.disabled_fields()
        return dict(form=host_category_form_read, values=hostcategory, action=submit_action, disabled_fields=self.disabled_fields(), host=hostcategory.host)

    @expose(template="mirrors.templates.hostcategory")
    @validate(form=host_category_form_new)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('hostid'):
            turbogears.flash("Error: form did not provide hostid")
            raise redirect("/")
        hostid = kwargs['hostid']
        del kwargs['hostid']

        try:
            host = Host.get(hostid)
        except SQLObjectNotFound:
            turbogears.flash("Error: invalid hostid - foul play?")
            raise turbogears.redirect("/")
            
        try:
            category = Category.get(kwargs['category'])
        except SQLObjectNotFound:
            turbogears.flash("Error: invalid category - foul play?")
            raise turbogears.redirect("/host_category/0/new?hostid=%s" % hostid)
            
        del kwargs['category']

        try:
            hostcategory = HostCategory(host=host, category=category, **kwargs)
        except:
            turbogears.flash("Error: Host already has category %s.  Try again." % category.name)
            raise turbogears.redirect("/host_category/0/new?hostid=%s" % hostid)
        turbogears.flash("HostCategory created.")
        raise turbogears.redirect("/host_category/%s" % hostcategory.id)


    @expose(template="mirrors.templates.hostcategory")
    @validate(form=host_category_form_read)
    @error_handler()
    def update(self, hostcategory, tg_errors=None, **kwargs):
        siteadmin_check(hostcategory.my_site(), identity)
        del kwargs['category']

        if tg_errors is not None:
            errstr = ""
            for k, v in tg_errors.iteritems():
                errstr += "%s: %s\n" % (k, v)
            turbogears.flash("Error: %s" % errstr)
            return dict(form=host_category_form_read, values=hostcategory, action = turbogears.url("/host_category/%s/update" % hostcategory.id),
                        disabled_fields=self.disabled_fields(), host=hostcategory.host)
        
        
        hostcategory.set(**kwargs)
        hostcategory.sync()
        turbogears.flash("HostCategory Updated")
        raise turbogears.redirect("/")

    @expose(template="mirrors.templates.hostcategory")
    def delete(self, hostcategory, **kwargs):
        siteadmin_check(hostcategory.my_site(), identity)
        hostid = hostcategory.host.id
        hostcategory.destroySelf()
        raise turbogears.redirect("/host/%s" % hostid)


class HostListitemController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()
    title = ""
    form = None

    def get(self, id):
        return self.do_get(id)
    
    @expose(template="mirrors.templates.boringhostform")
    def new(self, **kwargs):
        try:
            hostid=kwargs['hostid']
            host = Host.get(hostid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")

        siteadmin_check(host.my_site(), identity)
        submit_action = turbogears.url("%s/0/create?hostid=%s" % (self.submit_action_prefix, hostid))
        return dict(form=self.form, values=None, action=submit_action, title=self.title, host=host)
    
    @expose(template="mirrors.templates.boringhostform")
    @validate(form=form)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('hostid'):
            turbogears.flash("Error: form did not provide siteid")
            raise redirect("/")
        hostid = kwargs['hostid']

        try:
            host = Host.get(hostid)
        except sqlobject.SQLObjectNotFound:
            turbogears.flash("Error: Host %s does not exist" % hostid)
            raise redirect("/")

        downstream_siteadmin_check(host.my_site(), identity)

        try:
            self.do_create(host, kwargs)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error: entity already exists")
        raise turbogears.redirect("/host/%s" % host.id)

    @expose(template="mirrors.templates.boringhostform")
    def delete(self, thing, **kwargs):
        host = thing.host
        siteadmin_check(host.my_site(), identity)
        thing.destroySelf()
        raise turbogears.redirect("/host/%s" % host.id)



class HostAclIPFields(widgets.WidgetsList):
    ip = widgets.TextField(label="IP", validator=validators.All(validators.UnicodeString,validators.NotEmpty))

host_acl_ip_form = widgets.TableForm(fields=HostAclIPFields(),
                                     submit_text="Create Host ACL IP")

class HostAclIPController(HostListitemController):
    submit_action_prefix = "/host_acl_ip"
    title = "New Host ACL IP"
    form = host_acl_ip_form

    def do_get(self, id):
        v = HostAclIp.get(id)
        return dict(values=v, host=v.host)

    def do_create(self, host, kwargs):
        HostAclIp(host=host, ip=kwargs['ip'])



class HostNetblockFields(widgets.WidgetsList):
    netblock = widgets.TextField(validator=validators.All(validators.UnicodeString,validators.NotEmpty))

host_netblock_form = widgets.TableForm(fields=HostNetblockFields(),
                                       submit_text="Create Host Netblock")

class HostNetblockController(HostListitemController):
    submit_action_prefix="/host_netblock"
    title = "New Host Netblock"
    form = host_netblock_form

    def do_get(self, id):
        v = HostNetblock.get(id)
        return dict(values=v, host=v.host)

    def do_create(self, host, kwargs):
        HostNetblock(host=host, netblock=kwargs['netblock'])

class HostCountryAllowedFields(widgets.WidgetsList):
    country = widgets.TextField(validator=validators.Regex(r'^[a-zA-Z][a-zA-Z]$'),
                                help_text="2-letter ISO country code")

host_country_allowed_form = widgets.TableForm(fields=HostCountryAllowedFields(),
                                              submit_text="Create Country Allowed")

class HostCountryAllowedController(HostListitemController):
    submit_action_prefix="/host_country_allowed"
    title = "New Host Country Allowed"
    form = host_country_allowed_form

    def do_get(self, id):
        v = HostCountryAllowed.get(id)
        return dict(values=v, host=v.host)

    def do_create(self, host, kwargs):
        HostCountryAllowed(host=host, country=kwargs['country'])



#########################################################3
# HostCategoryURL
#########################################################3
class HostCategoryUrlFields(widgets.WidgetsList):
    url = widgets.TextField(validator=validators.UnicodeString, attrs=dict(size='30'))
    private  = widgets.CheckBox(default=False, label="For other mirrors only")

host_category_url_form = widgets.TableForm(fields=HostCategoryUrlFields(),
                                               submit_text="Create URL")

class HostCategoryUrlController(controllers.Controller, identity.SecureResource, content):
    require = identity.not_anonymous()
    title = "Host Category URL"
    form = host_category_url_form

    def get(self, id):
        v = HostCategoryUrl.get(id)
        return dict(values=v, host_category=v.host_category)
    
    @expose(template="mirrors.templates.hostcategoryurl")
    def new(self, **kwargs):
        try:
            hcid=kwargs['hcid']
            host_category = HostCategory.get(hcid)
        except sqlobject.SQLObjectNotFound:
            raise redirect("/")

        host = host_category.host
        siteadmin_check(host.my_site(), identity)
            
        submit_action = turbogears.url("/host_category_url/0/create?hcid=%s" % hcid)
        return dict(form=self.form, values=None, action=submit_action, title=self.title, host_category=host_category)

    @expose(template="mirrors.templates.hostcategoryurl")
    @validate(form=form)
    @error_handler(new)
    def create(self, **kwargs):
        if not kwargs.has_key('hcid'):
            turbogears.flash("Error: form didn't provide hcid")
            raise redirect("/")
        hcid = kwargs['hcid']

        try:
            hc = HostCategory.get(hcid)
        except sqlobject.SQLObjectNotFound:
            turbogears.flash("Error: HostCategory %s does not exist" % hcid)
            raise redirect("/")

        siteadmin_check(hc.my_site(), identity)

        if kwargs['url'].endswith('/'):
            kwargs['url'] = kwargs['url'][:-1]

        try:
            del kwargs['hcid']
            HostCategoryUrl(host_category=hc, **kwargs)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error: entity already exists")
        turbogears.flash("Success: HostCategoryURL created.")
        raise turbogears.redirect("/host_category/%s" % hcid)

    @expose(template="mirrors.templates.hostcategoryurl")
    def read(self, hcurl):
        downstream_siteadmin_check(hcurl.my_site(), identity)
        submit_action = turbogears.url("/host_category_url/%s/update" % hcurl.id)
        return dict(form=self.form, values=hcurl, action=submit_action, title=self.title, host_category=hcurl.host_category)
        
    @expose(template="mirrors.templates.hostcategoryurl")
    def update(self, hcurl, **kwargs):
        siteadmin_check(hcurl.my_site(), identity)
        if kwargs['url'].endswith('/'):
            kwargs['url'] = kwargs['url'][:-1]
        hcurl.set(**kwargs)
        hcurl.sync()
        submit_action = turbogears.url("/host_category_url/%s/update" % hcurl.id)
        return dict(form=self.form, values=hcurl, action=submit_action, title=self.title, host_category=hcurl.host_category)
        
            
    

    @expose(template="mirrors.templates.hostcategoryurl")
    def delete(self, hcurl, **kwargs):
        hc = hcurl.host_category
        siteadmin_check(hcurl.my_site(), identity)
        hcurl.destroySelf()
        raise turbogears.redirect("/host_category/%s" % hc.id)


#########################################################3
# SimpleDbObject
#########################################################3
class SimpleDbObjectController(controllers.Controller, identity.SecureResource, content):
    require = identity.in_group("sysadmin")
    title = "My Title"
    form = None
    myClass = None
    url_prefix=None

    def get(self, id):
        return dict(values=self.myClass.get(id))
    
    @expose(template="mirrors.templates.boringform")
    def new(self, **kwargs):
            
        submit_action = turbogears.url("/%s/0/create" % self.url_prefix)
        return dict(form=self.form, values=None, action=submit_action, title=self.title)

    def create(self, **kwargs):
        try:
            obj = self.myClass(**kwargs)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error: Object already exists")
            raise redirect("/")
        turbogears.flash("Success: Object created.")
        raise turbogears.redirect("/")

    @expose(template="mirrors.templates.boringform")
    def read(self, obj):
        submit_action = turbogears.url("/%s/%s/update" % (self.url_prefix, obj.id))
        return dict(form=self.form, values=obj, action=submit_action, title=self.title)
        
    def update(self, obj, **kwargs):
        obj.set(**kwargs)
        obj.sync()
        submit_action = turbogears.url("/%s/%s/update" % (self.url_prefix, obj.id))
        return dict(form=self.form, values=obj, action=submit_action, title=self.title)

    @expose(template="mirrors.templates.boringform")
    def delete(self, obj, **kwargs):
        obj.destroySelf()
        raise turbogears.redirect("/")

#########################################################3
# Arch
#########################################################3

class ArchFields(widgets.WidgetsList):
    name = widgets.TextField(validator=validators.UnicodeString, attrs=dict(size='30'))

arch_form = widgets.TableForm(fields=ArchFields(), submit_text="Create Arch")

class ArchController(SimpleDbObjectController):
    title="Arch"
    myClass = Arch
    url_prefix="arch"
    form = arch_form
    
    @expose(template="mirrors.templates.boringform")
    @validate(form=arch_form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        SimpleDbObjectController.create(self, **kwargs)

#########################################################3
# EmbargoedCountry
#########################################################3

class EmbargoedCountryFields(widgets.WidgetsList):
    country_code = widgets.TextField(validator=validators.Regex(r'^[a-zA-Z][a-zA-Z]$'),
                                     help_text="2-letter ISO country code" )

embargoed_country_form = widgets.TableForm(fields=EmbargoedCountryFields(), submit_text="Create Embargoed Country")

class EmbargoedCountryController(SimpleDbObjectController):
    title="Embargoed Country"
    myClass = EmbargoedCountry
    url_prefix="embargoed_country"
    form = embargoed_country_form
    
    @expose(template="mirrors.templates.boringform")
    @validate(form=embargoed_country_form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        SimpleDbObjectController.create(self, **kwargs)


#########################################################3
# Product
#########################################################3
class ProductFields(widgets.WidgetsList):
    name = widgets.TextField(validator=validators.All(validators.UnicodeString, validators.NotEmpty),
                             attrs=dict(size='30'))

product_form = widgets.TableForm(fields=ProductFields(), submit_text="Create Product")

class ProductController(SimpleDbObjectController):
    title = "Product"
    form = product_form
    myClass = Product
    url_prefix="product"

    @expose(template="mirrors.templates.boringform")
    @validate(form=product_form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        SimpleDbObjectController.create(self, **kwargs)


#########################################################3
# Repository
#########################################################3
class RepositoryFields(widgets.WidgetsList):
    name = widgets.TextField(validator=validators.All(validators.UnicodeString, validators.NotEmpty), attrs=dict(size='30'))
    prefix = widgets.TextField(validator=validators.All(validators.UnicodeString, validators.NotEmpty), attrs=dict(size='30'))
    category = LabelObjName()
    version = LabelObjName()
    arch = LabelObjName()
    directory = LabelObjName()

repository_form = widgets.TableForm(fields=RepositoryFields(), submit_text="Edit Repository")

class RepositoryController(SimpleDbObjectController):
    title = "Repository"
    form = repository_form
    myClass = Repository
    url_prefix="repository"

    @expose(template="mirrors.templates.boringform")
    @validate(form=repository_form)
    @error_handler(SimpleDbObjectController.new)
    def create(self, **kwargs):
        return SimpleDbObjectController.create(self, **kwargs)
    

    @expose(template="mirrors.templates.boringform")
    @validate(form=repository_form)
    @error_handler(SimpleDbObjectController.new)
    def update(self, obj, **kwargs):
        return SimpleDbObjectController.update(self, obj, **kwargs)


#########################################################3
# Version
#########################################################3
class VersionFields(widgets.WidgetsList):
    def get_products_options():
        return [(p.id, p.name) for p in Product.select(orderBy='name')]

    product = widgets.SingleSelectField(options=get_products_options)
    name = widgets.TextField(validator=validators.UnicodeString, attrs=dict(size='30'))
    isTest = widgets.CheckBox(label="is a Test release")

version_form = widgets.TableForm(fields=VersionFields(), submit_text="Create Version")


class VersionController(controllers.Controller, identity.SecureResource, content):
    require = identity.in_group("sysadmin")
    title = "Version"
    form = version_form

    def get(self, id):
        return dict(values=Version.get(id))
    
    @expose(template="mirrors.templates.boringform")
    def new(self, **kwargs):
            
        submit_action = turbogears.url("/version/0/create")
        return dict(form=self.form, values=None, action=submit_action, title=self.title)

    @expose(template="mirrors.templates.boringform")
    def read(self, version):
        submit_action = turbogears.url("/version/%s/update" % version.id)
        return dict(form=version_form, values=version, action=submit_action, title=self.title)


    @expose(template="mirrors.templates.boringform")
    @validate(form=form)
    @error_handler(new)
    def create(self, **kwargs):
        try:
            product=Product.get(kwargs['product'])
        except SQLObjectNotFound:
            turbogears.flash("Error: invalid product - foul play?")
            raise redirect("/")

        del kwargs['product']

        try:
            version = Version(product=product, **kwargs)
        except: # probably sqlite IntegrityError but we can't catch that for some reason... 
            turbogears.flash("Error: Version %s already exists" % name)
            raise redirect("/")
        turbogears.flash("Success: Version created.")
        raise turbogears.redirect("/")


    @expose(template="mirrors.templates.boringform")
    def delete(self, version, **kwargs):
        version.destroySelf()
        raise turbogears.redirect("/")




# This exports the /pub/fedora/linux/core/... directory tree.
# For each directory requested, it returns the mirrors of that directory.

class PubController(controllers.Controller):
    @expose(template="mirrors.templates.mirrorlist", format="plain", content_type="text/plain")
    def default(self, *vpath, **params):
        path = 'pub/' + '/'.join(vpath)
        country = None
        include_private=False
        if params.has_key('country'):
            country = params['country']
        if params.has_key('include_private'):
            include_private = params['include_private']

        try:
            directory = Directory.byName(path)
        except SQLObjectNotFound:
            return dict(values=[])
            
        urls = directory_mirror_urls(directory, country=country, include_private=include_private)
        for u, country, host in urls:
            if not u.startswith('http://') and not u.startswith('ftp://'):
                urls.remove((u, country, host))
        return dict(values=[u for u, country, host in urls])

class PublicListController(controllers.Controller):
    @expose(template="mirrors.templates.publiclist")
    def index(self, *vpath, **params):
        hosts = hosts=[h for h in Host.select(orderBy='country') if not h.is_private() and h.is_active()]
        
        return dict(hosts=hosts, numhosts=len(hosts),
                    products=list(Product.select(orderBy='name')), title='', arches=primary_arches)

    @expose(template="mirrors.templates.publiclist")
    def default(self, *vpath, **params):
        product = ver = arch = None
        if len(vpath) == 1:
            product = vpath[0]
            title=product
        elif len(vpath) == 2:
            product = vpath[0]
            ver = vpath[1]
            title = '%s/%s' % (product, ver)
        elif len(vpath) == 3:
            product = vpath[0]
            ver = vpath[1]
            arch = vpath[2]
            title = '%s/%s/%s' % (product, ver, arch)
        else:
            raise redirect('/publiclist')

        hosts = [h for h in Host.select(orderBy='country') if not h.is_private() and h.is_active() and \
                           len(h.product_version_arch_dirs(product, ver, arch)) > 0]

        return dict(hosts=hosts, numhosts=len(hosts),
                    products=list(Product.select(orderBy='name')),
                    arches=primary_arches, title=title)


        
from repomap import repomap
import GeoIP

gi = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)


#http://mirrors.fedoraproject.org/mirrorlist?repo=core-$releasever&arch=$basearch
#http://mirrors.fedoraproject.org/mirrorlist?repo=core-debug-$releasever&arch=$basearch

def do_mirrorlist(*args, **kwargs):
    if not kwargs.has_key('repo') or not kwargs.has_key('arch'):
        return dict(values=['#no repositories match'])

    try:
        arch = Arch.byName(kwargs['arch'])
    except SQLObjectNotFound:
        return dict(values=['#no repositories match'])

    s = kwargs['repo'].rfind('-') + 1
    prefix = kwargs['repo'][:s]
    version= kwargs['repo'][s:]
    addFc = False
    if version.startswith('fc'):
        version = version[2:]
        addFc = True


    pname = None
    cname = None
    
    try:
        pname = repomap[prefix][0]
        cname = repomap[prefix][1]
    except KeyError:
        return dict(values=['#no repositories match'])

    try:
        product = Product.byName(pname)
    except KeyError:
        return dict(values=['#no repositories match'])

    try:
        category = Category.byName(cname)
    except SQLObjectNotFound:
        return dict(values=['#no repositories match'])
    try:
        version = Version.selectBy(product=product, name=version)[0]
    except SQLObjectNotFound:
        return dict(values=['#no repositories match'])
    except KeyError:
        return dict(values=['#no repositories match'])

    if addFc:
        prefix += 'fc' + version.name
    else:
        prefix += version.name
    repos = Repository.selectBy(prefix=prefix, category=category, version=version, arch=arch)
    if repos.count() == 0:
        return dict(values=['#no repositories match'])
    
    seen_countries = urllist(repos[0])
    returnedCountryList = []
    countryCode = ''

    # fixme
    # this works, but doesn't trim list by per-host allowed-countries,
    # and doesn't add by continent if the list is too short
    # this probably needs to be in its own function
    if kwargs.has_key('country'):
        countryCode = kwargs['country'].upper()
        if countryCode == 'GLOBAL' or len(countryCode) < 2:
            countryCode = ''
        else:
            countryCode = countryCode[:2]
    else:
        client_ip = cherrypy.request.remote_addr
        countryCode = gi.country_code_by_addr(client_ip)
        if countryCode == None: countryCode = ''

    if countryCode == '' or not seen_countries.has_key(countryCode):
        returnedCountryList = ['# repo = %s country = global arch = %s' % (prefix, arch.name) ]
        for c in seen_countries.values():
            returnedCountryList.extend(c)
    else:
        returnedCountryList = ['# repo = %s country = %s arch = %s' % (prefix, countryCode, arch.name) ]
        returnedCountryList.extend(seen_countries[countryCode])
        if len(returnedCountryList) < 3:
            returnedCountryList = ['# repo = %s country = global arch = %s' % (prefix, arch.name) ]
            # fixme use little geoip thing here
            # once python-GeoIP exports it sanely
            for c in seen_countries.values():
                returnedCountryList.extend(c)

    return dict(values=returnedCountryList)
        

class Root(controllers.RootController):
    site = SiteController()
    siteadmin = SiteAdminController()
    host = HostController()
    pub = PubController()
    host_country_allowed = HostCountryAllowedController()
    host_acl_ip = HostAclIPController()
    host_netblock = HostNetblockController()
    host_category = HostCategoryController()
    host_category_url = HostCategoryUrlController()
    site2site = SiteToSiteController()
    product = ProductController()
    version = VersionController()
    arch = ArchController()
    embargoed_country = EmbargoedCountryController()
    repository = RepositoryController()
    from mirrors.xmlrpc import XmlrpcController
    xmlrpc = XmlrpcController()
    publiclist = PublicListController()
    
    @expose(template="mirrors.templates.welcome")
    @identity.require(identity.not_anonymous())
    def index(self):
        if "sysadmin" in identity.current.groups:
            sites = Site.select(orderBy='name')
        else:
            sites = user_sites(identity)

            
        if "sysadmin" in identity.current.groups:
            return {"sites":sites,
                    "arches":Arch.select(),
                    "products":Product.select(),
                    "versions":Version.select(),
                    "directories":Directory.select(orderBy='name'),
                    "categories":Category.select(),
                    "repositories":Repository.select(orderBy='name'),
                    "embargoed_countries":EmbargoedCountry.select(),
                    }
        else:
            return {"sites":sites}

    @expose(template="mirrors.templates.rsync_acl", format="plain", content_type="text/plain")
    def rsync_acl(self):
        rsync_acl_list = []
        for h in Host.select():
            if h.is_active():
                for n in h.acl_ips:
                    rsync_acl_list.append(n.ip)
        return dict(values=rsync_acl_list)

    @expose(template="mirrors.templates.rsync_acl", format="plain", content_type="text/plain")
    def mirrorlist(self, *args, **kwargs):
        return do_mirrorlist(*args, **kwargs)

    @expose(template="mirrors.templates.login")
    def login(self, forward_url=None, previous_url=None, *args, **kw):

        if not identity.current.anonymous \
            and identity.was_login_attempted() \
            and not identity.get_identity_errors():
            raise redirect(forward_url)

        forward_url=None
        previous_url= cherrypy.request.path

        if identity.was_login_attempted():
            msg=_("The credentials you supplied were not correct or "
                   "did not grant access to this resource.")
        elif identity.get_identity_errors():
            msg=_("You must provide your Fedora Account System credentials before accessing "
                   "this resource.")
        else:
            msg=_("Please log in.")
            forward_url= cherrypy.request.headers.get("Referer", "/")
        cherrypy.response.status=403
        return dict(message=msg, previous_url=previous_url, logging_in=True,
                    original_parameters=cherrypy.request.params,
                    forward_url=forward_url)

    @expose()
    def logout(self):
        identity.current.logout()
        raise redirect("/")
    
    @expose(template="mirrors.templates.register")
    def register(self, username="", display_name="", email_address="", tg_errors=None):
        if tg_errors:
            turbogears.flash(createErrorString(tg_errors))
            username, display_name, email_address = map(cherrypy.request.input_values.get, ["username", "display_name", "email_address"])
                    
        return dict(username=username, display_name=display_name, email_address=email_address)

    @expose()
    @error_handler(register)
    @validate(validators=my_validators.RegisterSchema)
    def doRegister(self, username, display_name, password1, password2, email_address):
        username = str(username)
        email_address = str(email_address)
        redirect_to_register = lambda:redirect("/register", {"username":username, "display_name":display_name, "email_address":email_address})
        
        try:
            User.by_user_name(username)
        except sqlobject.SQLObjectNotFound:
            pass
        else:
            turbogears.flash("Error:User %s Already Exists"%username)
            raise redirect_to_register()
        
        
        try:
            User.by_email_address(email_address)
        except sqlobject.SQLObjectNotFound:
            pass
        else:
            turbogears.flash("Error:Email-Address %s Already Exists"%username)
            raise redirect_to_register()
        
        #create user
        user = User(user_name=username, email_address=email_address, display_name=str(display_name), password=str(password1))
        
        #add user to user group
        user.addGroup(Group.by_group_name("user"))
        
        raise redirect("/")
        

