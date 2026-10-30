"""Bind a corpus distance decision to the exact source, image and Jenkins release admitted for publication."""
from hashlib import sha256
import json


def require_distance_receipt(receipt, identity, source_digest, build, report_raw):
    """Reject copied, denied, counterfactual, edited or image-mismatched admission receipts."""
    if (receipt.get('phase') != 'corpus-distance' or receipt.get('passed') is not True
            or any(receipt.get(k) != v for k, v in identity.items())
            or receipt.get('source_digest') != source_digest or receipt.get('scenario') != 'production'
            or receipt.get('image_id') != build['image_id']
            or receipt.get('report_sha256') != sha256(report_raw).hexdigest()):
        raise ValueError('A production corpus decision from this exact tested image and release is required')
    report = json.loads(report_raw)
    if set(report['candidates']) != {'production'}:
        raise ValueError('A synthetic suite cannot admit a production release')
    decision = report['candidates']['production']['decision']
    if (decision.get('allowed') is not True or decision.get('requires_review') is not False
            or report['production_catalog_sha256'] != build['packaged_sha256']['knowledge/catalog.json']):
        raise ValueError('The candidate corpus is denied or belongs to a different image')
    return receipt
