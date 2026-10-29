"""Acknowledge binary CloudEvents only after the durable drift comparison completes."""
import re
from fastapi import HTTPException,Request,Response
from services.drift_api.contracts import CompareRequest


def validate_envelope(headers):
    """Reject another event contract before any persistent comparison is attempted."""
    if (headers.get('ce-specversion')!='1.0' or headers.get('ce-type')!='dev.edai.iot.drift.window'
            or headers.get('ce-source')!='urn:edai:iot:window-publisher'
            or re.fullmatch(r'[A-Za-z0-9_:-]{1,100}',headers.get('ce-id','')) is None
            or headers.get('content-type','').split(';')[0].strip().lower()!='application/json'):
        raise HTTPException(400,'Expected the declared binary drift CloudEvent')


async def acknowledge(invoke,body,headers):
    """Acknowledge only after durable completion; a failed write remains an HTTP error for delivery retries."""
    validate_envelope(headers)
    await invoke('compare',body)
    return Response(status_code=204)


def install(app,invoke):
    """Keep the synchronous JSON API while giving Eventing an empty successful reply."""
    @app.post('/v1/events/drift',status_code=204)
    async def receive(body:CompareRequest,request:Request):
        """Pass the validated data and binary metadata to the bounded persistent runtime."""
        return await acknowledge(invoke,body,request.headers)
