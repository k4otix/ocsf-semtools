# Observables

OCSF marks some attributes as observables: IP addresses, hostnames, file hashes, users and so on. An
event lists them in its `observables` array. The library works out that list from the schema and the
event, so you don't maintain it by hand.

```pycon
>>> from ocsf_toolkit import build, load
>>> schema = load("1.9.0")
>>> event = build(
...     (schema, "network_activity"),
...     activity_id=6,
...     severity_id=1,
...     time=1_700_000_000_000,
...     metadata={"product": {"name": "Sensor", "vendor_name": "Acme"}},
...     src_endpoint={"ip": "10.0.0.1"},
...     dst_endpoint={"ip": "192.0.2.7", "port": 443},
...     observables=[{"name": "dst_endpoint.ip", "type_id": 2, "value": "192.0.2.7"}],
... )

```

### `extract`

`extract(event, schema, class_key=None, *, max_nodes=None)` returns `(observables, report)` in the
order the event is walked, and leaves the event unchanged. An attribute is an observable when the
schema marks its type, its dictionary entry, its object, or the attribute within its class or object,
or when the class's own observable path map names it.
Values are given as strings, the way OCSF writes them, and objects such as endpoints carry no value.

```pycon
>>> from ocsf_toolkit import extract
>>> found, report = extract(event, schema)
>>> for observable in found:
...     print(observable.name, observable.type_id, observable.type, observable.value)
src_endpoint 20 Endpoint None
dst_endpoint 20 Endpoint None
src_endpoint.ip 2 IP Address 10.0.0.1
dst_endpoint.ip 2 IP Address 192.0.2.7
dst_endpoint.port 11 Port 443

```

The report holds an `observable_mismatch` warning for each entry the event already lists that no
attribute in the event backs. `max_nodes` caps the number of attribute visits; past it the walk stops
with a `max_nodes_exceeded` finding instead of running on.

### `Observable`

`Observable(name, type_id, type, value)`: `name` is the attribute path in OCSF's dotted notation,
with `[n]` for array positions. `to_dict()` gives the OCSF `observable` object, leaving out a `None`
value.

```pycon
>>> found[4].to_dict()
{'name': 'dst_endpoint.port', 'type_id': 11, 'type': 'Port', 'value': '443'}

```

### `merge`

`merge(event, found)` returns a copy of the event with the found observables appended to the ones the
event already lists. The caller's entries stay as given, and an entry already present, matched on name,
`type_id` and value, isn't added twice:

```pycon
>>> from ocsf_toolkit import merge
>>> merged = merge(event, found)
>>> [entry["name"] for entry in merged["observables"]]
['dst_endpoint.ip', 'src_endpoint', 'dst_endpoint', 'src_endpoint.ip', 'dst_endpoint.port']
>>> event["observables"] == merged["observables"][:1]
True

```
