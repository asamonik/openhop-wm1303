"""Resolve the configured advert scope using the repeater's region keys."""

import base64


def packet_scope(record, storage):
    """Resolve the packet's first transport code without revealing region keys.

    Codes are packet-specific 16-bit HMACs, not region IDs. A collision between
    differently named configured regions cannot identify a unique scope.
    """
    unknown = {'scope': 'unknown scope', 'scope_known': False}
    try:
        from openhop_core.protocol import Packet
        from openhop_core.protocol.transport_keys import calc_transport_code

        raw = record.get('raw_packet')
        if isinstance(raw, str):
            raw = bytes.fromhex(raw)
        if not isinstance(raw, (bytes, bytearray)) or not raw or raw[0] & 3 not in (0, 3):
            return unknown
        packet = Packet()
        if not packet.read_from(raw) or not packet.has_transport_codes():
            return unknown
        names = set()
        for region in storage.get_transport_keys():
            try:
                key = base64.b64decode(region.get('transport_key', ''), validate=True)
                name = str(region.get('name') or '').strip()
                if name and len(key) == 16 and calc_transport_code(key, packet) == packet.transport_codes[0]:
                    names.add(name)
            except (TypeError, ValueError):
                continue
        if len(names) == 1:
            return {'scope': names.pop(), 'scope_known': True}
    except (ImportError, TypeError, ValueError, AttributeError):
        pass
    return unknown


def resolve_default_region(value, storage):
    """Return an existing, allowed region, or None for unscoped adverts."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("default_region must be a region name or null")
    name = value.strip()
    if name in ("", "<null>"):
        return None
    needle = name.removeprefix("#").lower()
    for record in storage.get_transport_keys():
        if str(record.get("name", "")).removeprefix("#").lower() != needle:
            continue
        if record.get("flood_policy") != "allow":
            raise ValueError("Enable flooding for the selected default region first")
        key = base64.b64decode(record.get("transport_key", ""), validate=True)
        if len(key) != 16:
            raise ValueError("Default region must have a 16-byte transport key")
        return record
    raise ValueError("Add the default region to the region list first")


def apply_default_advert_scope(packet, config, storage):
    """Scope an advert; invalid settings fail instead of broadcasting unscoped."""
    record = resolve_default_region(config.get("mesh", {}).get("default_region"), storage)
    if record is None:
        return None
    from openhop_core.protocol.constants import ROUTE_TYPE_TRANSPORT_FLOOD
    from openhop_core.protocol.transport_keys import calc_transport_code

    key = base64.b64decode(record["transport_key"], validate=True)
    packet.transport_codes = [calc_transport_code(key, packet), 0]
    packet.header = (packet.header & ~0x03) | ROUTE_TYPE_TRANSPORT_FLOOD
    return record["name"]
