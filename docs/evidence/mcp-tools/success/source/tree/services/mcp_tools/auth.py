"""Require scoped bearer credentials and bounded HTTP bodies before REST or MCP request handling."""
from hmac import compare_digest
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

MAX_BODY_BYTES = 65_536
PUBLIC_PATHS = {'/health/live','/health/ready','/metrics','/docs','/openapi.json','/docs/oauth2-redirect'}


class BoundedBearerMiddleware:
    """Authenticate before parsing input and enforce the actual streamed body length, not only its header."""
    def __init__(self, app, token):
        """Keep the existing secret only in process memory and refuse weak/missing configuration."""
        if not isinstance(token, str) or len(token) < 32: raise ValueError('A scoped bearer secret is required')
        self.app = app; self.expected = ('Bearer '+token).encode()

    async def __call__(self, scope, receive, send):
        """Reject unauthorized, oversized or ambiguously framed requests before a tool can run."""
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        if scope['path'] not in PUBLIC_PATHS and not compare_digest(headers.get('authorization','').encode(), self.expected):
            return await JSONResponse({'detail':'Valid bearer authorization is required'},401,headers={'WWW-Authenticate':'Bearer'})(scope,receive,send)
        if scope['method'] == 'POST':
            declared = headers.get('content-length','')
            if not declared.isascii() or not declared.isdigit():
                return await JSONResponse({'detail':'A fixed Content-Length is required'},411)(scope,receive,send)
            if len(declared) > 8 or int(declared) > MAX_BODY_BYTES:
                return await JSONResponse({'detail':'Request exceeds 64 KiB'},413)(scope,receive,send)
            chunks = []; total = 0
            while True:
                event = await receive()
                if event['type'] == 'http.disconnect': return
                value = event.get('body',b''); total += len(value)
                if total > MAX_BODY_BYTES:
                    return await JSONResponse({'detail':'Request exceeds 64 KiB'},413)(scope,receive,send)
                chunks.append(value)
                if not event.get('more_body',False): break
            if total != int(declared):
                return await JSONResponse({'detail':'Content-Length differs from the actual body'},400)(scope,receive,send)
            consumed = False
            async def replay():
                """Pass the checked body once, then preserve downstream disconnect observation."""
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {'type':'http.request','body':b''.join(chunks),'more_body':False}
                return await receive()
            return await self.app(scope,replay,send)
        return await self.app(scope,receive,send)
