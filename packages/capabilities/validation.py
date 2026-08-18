from typing import Any


class PluginSchemaError(ValueError): pass


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict): raise PluginSchemaError("Plugin arguments must be an object")
    properties=schema.get("properties") or {}; required=set(schema.get("required") or [])
    missing=required-set(arguments)
    if missing: raise PluginSchemaError("Missing required arguments: "+", ".join(sorted(missing)))
    if schema.get("additionalProperties") is False:
        unknown=set(arguments)-set(properties)
        if unknown: raise PluginSchemaError("Unknown arguments: "+", ".join(sorted(unknown)))
    python_types={"string":str,"integer":int,"number":(int,float),"boolean":bool,"object":dict,"array":list}
    for name,value in arguments.items():
        rule=properties.get(name,{})
        expected=python_types.get(rule.get("type"))
        if expected and (not isinstance(value,expected) or rule.get("type")=="integer" and isinstance(value,bool)):
            raise PluginSchemaError(f"{name} must be {rule['type']}")
        if "enum" in rule and value not in rule["enum"]: raise PluginSchemaError(f"{name} is not an allowed value")
        if isinstance(value,(int,float)):
            if "minimum" in rule and value<rule["minimum"]: raise PluginSchemaError(f"{name} is below minimum")
            if "maximum" in rule and value>rule["maximum"]: raise PluginSchemaError(f"{name} exceeds maximum")
