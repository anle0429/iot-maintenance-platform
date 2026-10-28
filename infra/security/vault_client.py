"""Read narrowly authorized Vault KV2 secrets over verified private HTTPS without logging credentials."""
from ipaddress import ip_address
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler
from pathlib import Path
import json,re,ssl


class _NoRedirect(HTTPRedirectHandler):
    """Never forward Vault tokens to a redirect target, including another private host."""
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        return None


class VaultRequestError(RuntimeError):
    """Expose only the HTTP status; Vault response bodies may contain sensitive values."""
    def __init__(self,status):
        self.status=status
        super().__init__('Private Vault request failed with HTTP '+str(status))


class VaultClient:
    """Exchange an AppRole identity for a short-lived token and read KV2 values under server-enforced policy."""
    def __init__(self,address,ca,role):
        parsed=urlsplit(address)
        try:ip=ip_address(parsed.hostname or '')
        except ValueError:raise ValueError('Use an explicit private Vault IP with certificate SAN') from None
        if parsed.scheme!='https' or not ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.version!=4 or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not 1<=(parsed.port or 443)<=65535:
            raise ValueError('Require private HTTPS Vault with no userinfo/path/query')
        if set(role)!={'role_id','secret_id'} or any(not re.fullmatch(r'[a-f0-9-]{36}',role[n]) for n in role):raise ValueError('Require only opaque AppRole UUID identifiers')
        self.address=address;self.context=ssl.create_default_context(cafile=str(ca));self._role=dict(role);self._token=None
        self._open=build_opener(HTTPSHandler(context=self.context),_NoRedirect()).open
        if not self.context.check_hostname or self.context.verify_mode!=ssl.CERT_REQUIRED:raise ValueError('Certificate and hostname verification required')

    def _request(self,path,body=None,method=None):
        """Bound responses and suppress secret-bearing error bodies and transport details."""
        headers={'Content-Type':'application/json'}
        if self._token:headers['X-Vault-Token']=self._token
        request=Request(self.address+'/v1/'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
        try:
            with self._open(request,timeout=15) as response:
                raw=response.read(1_048_577)
                if len(raw)>1_048_576:raise ValueError('Private Vault response exceeds1MiB')
                return json.loads(raw) if raw else {}
        except HTTPError as error:raise VaultRequestError(error.code) from None
        except URLError:raise RuntimeError('Private Vault transport failed') from None

    def login(self):
        """Keep the returned token only in memory; return safe lease metadata to the caller."""
        value=self._request('auth/approle/login',self._role)['auth'];self._token=value['client_token']
        return {'lease_duration':value['lease_duration'],'renewable':value['renewable'],'token_policies':value['token_policies']}

    def read(self,path):
        """Return data/version only for an explicit KV path; the Vault policy rejects cross-service access."""
        if not re.fullmatch(r'iot/data/[a-z0-9_-]+/[a-z0-9_-]+',path):raise ValueError('Require one explicit KV2 service secret path')
        if self._token is None:raise RuntimeError('Login before reading private Vault')
        value=self._request(path)['data'];return value['data'],value['metadata']['version']

    def revoke(self):
        """Revoke this token without requesting authority over any other token."""
        if self._token is None:raise RuntimeError('No active Vault token')
        self._request('auth/token/revoke-self',{},'POST');self._token=None
