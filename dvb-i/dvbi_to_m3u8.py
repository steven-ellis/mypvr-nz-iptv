#!/usr/bin/env python3

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
import xml.etree.ElementTree as ET


# ----------------------------
# Namespace-safe tag helper
# ----------------------------
def localname(tag):
    return tag.split("}")[-1]


def is_uri_tag(tag):
    t = tag.lower()
    return (
        t.endswith("uri") or
        t.endswith(":uri") or
        localname(tag).lower() == "uri"
    )


# ----------------------------
# Load XML (file or URL)
# ----------------------------
def load_xml(source):
    parsed = urlparse(source)

    if parsed.scheme in ("http", "https"):
        r = requests.get(source, timeout=30)
        r.raise_for_status()
        return r.content

    return Path(source).read_bytes()


# ----------------------------
# Sanitize tvg-id
# ----------------------------
def sanitize_tvg_id(text):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


# ----------------------------
# Service Name
# ----------------------------
def find_service_name(service):
    for elem in service.iter():
        if localname(elem.tag) in ("ServiceName", "DisplayName", "Name"):
            if elem.text and elem.text.strip():
                return elem.text.strip()
    return "Unknown"


# ----------------------------
# Logo (MediaUri image/png)
# ----------------------------
def find_logo(service):
    fallback = ""

    for elem in service.iter():

        if localname(elem.tag) != "MediaUri":
            continue

        uri = (elem.text or "").strip()
        if not uri:
            continue

        ct = elem.attrib.get("contentType", "")

        if ct == "image/png":
            return uri

        if not fallback:
            fallback = uri

    return fallback


# ----------------------------
# LCN mapping
# ----------------------------
def build_lcn_table(root):
    lcn_map = {}

    for elem in root.iter():

        if localname(elem.tag) != "LCN":
            continue

        service_ref = elem.attrib.get("serviceRef")
        channel = elem.attrib.get("channelNumber")

        if service_ref and channel:
            lcn_map[service_ref] = channel

    return lcn_map


# ----------------------------
# STREAM RESOLVER (FINAL)
# ----------------------------
def find_stream_uri(service, debug=False):

    instances = []

    for elem in service.iter():
        if localname(elem.tag) == "ServiceInstance":
            try:
                priority = int(elem.attrib.get("priority", "999"))
            except ValueError:
                priority = 999
            instances.append((priority, elem))

    instances.sort(key=lambda x: x[0])

    for priority, instance in instances:

        if debug:
            print(f"\n[DEBUG] ServiceInstance priority={priority}")

        # ----------------------
        # 1. Direct audio (radio)
        # ----------------------
        for elem in instance.iter():

            if localname(elem.tag) == "IdentifierBasedDeliveryParameters":

                if elem.text and "http" in elem.text:

                    uri = elem.text.strip()

                    if debug:
                        print(f"[DEBUG] AUDIO: {uri}")

                    return uri, "radio"

        # ----------------------
        # 2. HLS radio (RTÉ / Saorview)
        # ----------------------
        for elem in instance.iter():

            if localname(elem.tag) == "OtherDeliveryParameters":

                for child in elem.iter():

                    if is_uri_tag(child.tag):

                        if child.text and child.text.strip():

                            uri = child.text.strip()

                            if uri.startswith("http"):

                                if debug:
                                    print(f"[DEBUG] RADIO HLS: {uri}")

                                return uri, "radio"

        # ----------------------
        # 3. TV streams
        # ----------------------
        for elem in instance.iter():

            if localname(elem.tag) in (
                "DVBTDeliveryParameters",
                "DASHDeliveryParameters",
                "HLSDeliveryParameters",
                "HTTPLSDeliveryParameters",
            ):

                for child in elem.iter():

                    if is_uri_tag(child.tag):

                        if child.text and child.text.strip():

                            uri = child.text.strip()

                            if debug:
                                print(f"[DEBUG] TV: {uri}")

                            return uri, "video"

    return None, None


# ----------------------------
# Extract services
# ----------------------------
def extract_services(xml_data, debug=False, lcn_offset=0):

    root = ET.fromstring(xml_data)

    lcn_map = build_lcn_table(root)

    services = []

    for service in root.iter():

        if localname(service.tag) != "Service":
            continue

        name = find_service_name(service)
        logo = find_logo(service)

        unique_id = None

        for e in service.iter():
            if localname(e.tag) == "UniqueIdentifier":
                unique_id = (e.text or "").strip()

        lcn = lcn_map.get(unique_id)

        # ----------------------
        # Apply LCN offset (FIXED)
        # ----------------------
        if lcn is not None:
            try:
                lcn = str(int(lcn) + lcn_offset)
            except ValueError:
                pass

        uri, stream_type = find_stream_uri(service, debug=debug)

        if not uri:
            continue

        services.append({
            "name": name,
            "tvg_id": unique_id or sanitize_tvg_id(name),
            "logo": logo,
            "lcn": lcn,
            "url": uri,
            "type": stream_type,
        })

    # sort by LCN
    def sort_key(s):
        try:
            return int(s["lcn"])
        except:
            return 999999

    return sorted(services, key=sort_key)


# ----------------------------
# Write M3U
# ----------------------------
def write_m3u(services, out):

    with open(out, "w", encoding="utf-8") as f:

        f.write("#EXTM3U\n")

        for s in services:

            group = "Radio" if s["type"] == "radio" else "DVB-I"

            attrs = [
                f'tvg-id="{s["tvg_id"]}"',
                f'tvg-name="{s["name"]}"',
                f'group-title="{group}"'
            ]

            if s["logo"]:
                attrs.append(f'tvg-logo="{s["logo"]}"')

            if s["lcn"]:
                attrs.append(f'tvg-chno="{s["lcn"]}"')

            f.write("#EXTINF:-1 " + " ".join(attrs) + f",{s['name']}\n")
            f.write(s["url"] + "\n")


# ----------------------------
# Main
# ----------------------------
def main():

    ap = argparse.ArgumentParser()

    ap.add_argument("source")
    ap.add_argument("output")
    ap.add_argument("--debug", action="store_true")

    # RESTORED FEATURE
    ap.add_argument(
        "--lcn-offset",
        type=int,
        default=0,
        help="Offset applied to LCN values (e.g. 200 makes 9 → 209)"
    )

    args = ap.parse_args()

    xml = load_xml(args.source)

    services = extract_services(
        xml,
        debug=args.debug,
        lcn_offset=args.lcn_offset
    )

    write_m3u(services, args.output)

    print(f"Wrote {len(services)} channels → {args.output}")


if __name__ == "__main__":
    main()
