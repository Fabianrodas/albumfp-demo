from flask import jsonify

def ok(data=None, message="OK", status=200, **meta):
    payload = {"ok": True, "message": message}
    if data is not None:
        payload["data"] = data
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status

def fail(message="Error", status=400, code=None, errors=None, **meta):
    payload = {"ok": False, "message": message}
    if code:
        payload["code"] = code
    if errors:
        payload["errors"] = errors
    if meta:
        payload["meta"] = meta
    return jsonify(payload), status