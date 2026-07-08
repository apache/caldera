import logging
import ldap3

from aiohttp import web
from aiohttp_jinja2 import render_template
from ldap3.core.exceptions import LDAPAttributeError, LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn_value

from app.service.interfaces.i_login_handler import LoginHandlerInterface
from app.utility.config_util import verify_hash

HANDLER_NAME = 'Default Login Handler'


class DefaultLoginHandler(LoginHandlerInterface):
    def __init__(self, services):
        super().__init__(services, HANDLER_NAME)
        self.log = logging.getLogger('default_login_handler')
        self._ldap_config = self.get_config('ldap')

    async def handle_login(self, request, **kwargs):
        data = await request.post()
        username = data.get('username')
        password = data.get('password')
        if username and password:
            if self._ldap_config:
                verified = await self._ldap_login(username, password)
            else:
                verified = await self._check_credentials(request.app.user_map, username, password)

            if verified:
                auth_svc = self.services.get('auth_svc')
                if not auth_svc:
                    raise Exception('Auth service not available.')
                await auth_svc.handle_successful_login(request, username)
            self.log.debug('%s failed login attempt: ', username)
        raise web.HTTPFound('/')

    async def handle_login_redirect(self, request, **kwargs):
        """Handle login redirect.

        :return: login.html template if use_template is set to True in kwargs.
        :raises web.HTTPFound: HTTPFound exception to redirect to the '/login' page if use_template
            is set to False or not included in kwargs.
        """
        if kwargs.get('use_template'):
            return render_template('login.html', request, dict())
        else:
            raise web.HTTPFound('/login')

    @staticmethod
    async def _check_credentials(user_map, username, password):
        user = user_map.get(username)
        if not user:
            return False
        return verify_hash(user.password, password)

    async def _ldap_login(self, username, password):
        server = ldap3.Server(self._ldap_config.get('server'))
        dn = self._ldap_config.get('dn')
        user_attr = self._ldap_config.get('user_attr') or 'uid'
        user_format_string = self._ldap_config.get("user_format") or "{user_attr}={user},{dn}"
        safe_username = escape_rdn_value(username)
        try:
            user_string = user_format_string.format(user_attr=user_attr, user=safe_username, dn=dn)
        except KeyError:
            user_string = '%s=%s,%s' % (user_attr, safe_username, dn)

        try:
            with ldap3.Connection(server, user=user_string, password=password) as conn:
                if conn.bind():
                    auth_svc = self.services.get('auth_svc')
                    if username not in auth_svc.user_map:
                        group = await self._ldap_get_group(conn, dn, username, user_attr)
                        await auth_svc.create_user(username, None, group)
                    return True
        except LDAPException:
            self.log.error('Unable to connect to LDAP server')

        return False

    async def _ldap_get_group(self, connection, dn, username, user_attr):
        group_attr = self._ldap_config.get('group_attr') or 'objectClass'
        red_group_name = self._ldap_config.get('red_group') or 'red'

        try:
            connection.search(dn, '(%s=%s)' % (user_attr, escape_filter_chars(username)), attributes=[group_attr])
        except LDAPAttributeError:
            self.log.error('Invalid group_attr in config: %s', group_attr)
            return 'blue'

        groups_result = connection.entries[0][group_attr].value
        if ((isinstance(groups_result, list) and red_group_name in groups_result)
                or red_group_name == groups_result):
            return 'red'
        else:
            return 'blue'
