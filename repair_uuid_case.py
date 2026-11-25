"""Use existing repository's cloud/geometry preflight to repair UUID spelling only."""
import inspect
import repair_missing_slave_roles as original

source = inspect.getsource(original.collect)
old = 'uid = str(uuid.UUID(str(master.get("uid"))))'
new = '''uid = master.get("uid")
    if not isinstance(uid, str) or str(uuid.UUID(uid)) != uid.lower():
        raise ValueError("Invalid master UUID spelling")'''
old_check = '''normalized["uid"] = str(uuid.UUID(str(role.get("uid"))))
            if any(normalized.get(k) != v for k, v in expected.items()):
                raise ValueError(d["name"] + ": unexpected existing group role/size/offset/UUID")'''
new_check = '''actual_uid = role.get("uid")
            if not isinstance(actual_uid, str) or actual_uid.lower() != uid.lower():
                raise ValueError(d["name"] + ": different UUID")
            normalized["uid"] = uid
            if any(normalized.get(k) != v for k, v in expected.items()):
                raise ValueError(d["name"] + ": unexpected existing group role/size/offset/UUID")
            if actual_uid != uid:
                if i == 0 or mode != "slave":
                    raise ValueError("Unexpected role")
                missing[d["name"]] = (d, expected)
                print(f"UUID CASE MISMATCH: {d['name']} {actual_uid} -> {uid}")'''
# Deliberately refuse missing-role writes in this case-only tool.
old_missing = 'if i > 0 and mode == "none":\n            missing[d["name"]] = (d, expected)'
new_missing = 'if i > 0 and mode == "none":\n            raise ValueError(d["name"] + ": missing role; case-only repair refused")'
if all(x in source for x in (old, old_check, old_missing)):
    source = source.replace(old,new,1).replace(old_check,new_check,1).replace(old_missing,new_missing,1)
    namespace = dict(vars(original))
    exec(compile(source, '<case-only preflight>', 'exec'), namespace)
    original.collect = namespace['collect']
else:
    raise SystemExit('STOP: repository repair script differs from expected version; inspect before proceeding.')

if __name__ == '__main__':
    raise SystemExit(original.main())
