#!/usr/bin/env python3
"""Test ONE hypothesised Twinkly cloud refresh request using the private DPAPI cache.
No automatic retries: a refresh token may be single-use or rotated.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from cloud_auth import BASE, NoRedirect, MAX_RESPONSE, interactive_login
from cloud_token_store import load_session, store_session
from cloud_audit import cloud_get

PATHS={"auth":"/v2/auth","refresh":"/v2/auth/refresh","auth-token":"/v2/auth/token","oauth":"/oauth/token"}

def refresh(old, endpoint, encoding, timeout, shape="grant"):
    token=old.get("refresh_token")
    if not isinstance(token,str) or not token or len(token)>4096 or any(c.isspace() for c in token):
        raise ValueError("No valid cached refresh token; use --login")
    fields={"refresh_token":token}
    if shape=="grant":
        fields["grant_type"]="refresh_token"
    elif shape=="camel":
        fields={"refreshToken":token}
    if encoding=="json":
        body=json.dumps(fields).encode()
        content_type="application/json"
    else:
        body=urllib.parse.urlencode(fields).encode()
        content_type="application/x-www-form-urlencoded"
    path=PATHS[endpoint]
    print("Testing POST",BASE+path,"with",encoding,"encoding")
    request=urllib.request.Request(BASE+path,data=body,method="POST",headers={
        "Content-Type":content_type,"Accept":"application/json",
        "User-Agent":"twinkly-api-tools-refresh-test/0.1"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=timeout) as response:
            status=response.status
            raw=response.read(MAX_RESPONSE+1)
    except urllib.error.HTTPError as exc:
        print("HTTP",exc.code,"- renewal rejected; cached session not changed.")
        # Report only allowlisted field names and error codes, never raw values.
        try:
            error=json.loads(exc.read(MAX_RESPONSE))
            fields={"username","password","refresh_token","refreshToken","grant_type","token","client_id","client_secret","access_token"}
            identifiers={"invalid_grant","invalid_request","invalid_token","unsupported_grant_type","invalid_client","invalid_credentials","required","missing","expired","validation_error"}
            def walk(value):
                if isinstance(value,dict):
                    for key,item in value.items():
                        if key in fields:
                            print("Validation mentions field:",key)
                        if key in ("error","code","type") and isinstance(item,str) and item in identifiers:
                            print("Error identifier:",item)
                        if key in ("field","loc","param","path"):
                            for part in (item if isinstance(item,list) else [item]):
                                if isinstance(part,str) and part in fields:
                                    print("Validation mentions field:",part)
                        if isinstance(item,(dict,list)):
                            walk(item)
                elif isinstance(value,list):
                    for item in value:
                        walk(item)
            walk(error)
        except (ValueError,TypeError,OSError):
            print("No parseable JSON validation details.")
        return None
    print("HTTP",status)
    if len(raw)>MAX_RESPONSE:
        raise ValueError("Oversized reply")
    data=json.loads(raw)
    if not isinstance(data,dict):
        raise ValueError("Unexpected reply format")
    access=data.get("access_token")
    replacement=data.get("refresh_token",token)
    lifetime=data.get("expires_in")
    if not isinstance(access,str) or not access or len(access)>4096 or any(c.isspace() for c in access):
        raise ValueError("No valid access token returned")
    if str(data.get("token_type","")).casefold()!="bearer":
        raise ValueError("Unexpected token type")
    if not isinstance(replacement,str) or not replacement or len(replacement)>4096 or any(c.isspace() for c in replacement):
        raise ValueError("Invalid replacement refresh token")
    if isinstance(lifetime,bool) or not isinstance(lifetime,(int,float)) or not 0<lifetime<=2592000:
        raise ValueError("Invalid expiry")
    print("Access token changed:",access!=old["access_token"])
    print("Refresh token rotated:",replacement!=token)
    print("Advertised lifetime (seconds):",lifetime)
    return {"access_token":access,"refresh_token":replacement,"expires_in":lifetime}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoint",choices=PATHS,default="auth")
    p.add_argument("--encoding",choices=["json","form"],default="json")
    p.add_argument("--shape",choices=["grant","token-only","camel"],default="grant",help="Request field format")
    p.add_argument("--login",action="store_true",help="First obtain a fresh private cloud session")
    p.add_argument("--timeout",type=float,default=20)
    a=p.parse_args()
    try:
        if not 0<a.timeout<=30:
            p.error("Timeout must be between 0 and 30 seconds")
        if a.login:
            store_session(interactive_login(timeout=a.timeout,return_session=True))
        old=load_session()
        if not old:
            raise ValueError("No cached session; use --login")
        print("Cached access token seconds remaining:",round(old["expires_at"]-time.time()))
        session=refresh(old,a.endpoint,a.encoding,a.timeout,a.shape)
        if session is None:
            return 2
        # Verify against read-only inventory before replacing the encrypted cache.
        cloud_get("/v3/installations",session["access_token"],a.timeout)
        store_session(session)
        print("PASS: new access token accepted by cloud API; encrypted cache updated.")
        return 0
    except (ValueError,RuntimeError,OSError,TypeError,KeyError) as exc:
        # Do not print raw response bodies, URLs with parameters, or token values.
        print("BLOCK:",type(exc).__name__,"- test could not complete. If refresh was accepted but verification failed, interactive login may be needed.")
        return 2

if __name__=="__main__":
    sys.exit(main())
