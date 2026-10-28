"""Acknowledge binary CloudEvents only after the durable drift comparison completes."""
import re
from fastapi import HTTPException,Request,Response
from services.drift_api.contracts import CompareRequest


def install(app,invoke):
    """Keep the synchronous JSON API while giving Eventing an empty successful reply."""
    @app.post('/v1/events/drift',status_code=204)
    async def receive(body:CompareRequest,request:Request):
        """Validate routing metadata; retries deduplicate by reference/window, independent of event ID."""
        headers=request.headers
        if (headers.get('ce-specversion')!='1.0' or headers.get('ce-type')!='dev.edai.iot.drift.window'
                or headers.get('ce-source')!='urn:edai:iot:window-publisher'
                or re.fullmatch(r'[A-Za-z0-9_:-]{1,100}',headers.get('ce-id','')) is None
                or headers.get('content-type','').split(';')[0].strip().lower()!='application/json'):
            raise HTTPException(400,'Expected the declared binary drift CloudEvent')
        await invoke('compare',body)
        return Response(status_code=204)
