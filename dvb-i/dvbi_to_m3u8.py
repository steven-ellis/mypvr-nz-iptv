#!/usr/bin/env python3

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
import xml.etree.ElementTree as ET


#
# DVB-I XML namespaces
#
NAMESPACES = {
    "dvb": "urn:dvb:metadata:servicediscovery:2020",
    "types": "urn:dvb:metadata:servicediscovery:2020:types",
    "dvbi-types": "urn:dvb:metadata:servicediscovery:2020:types",
    "mpeg7": "urn:tva:mpeg7:2008",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}


def load_xml(source):
    """
    Load XML from URL or local file.
    """

    parsed = urlparse(source)

    if parsed.scheme in ("http", "https"):

        response = requests.get(source, timeout=30)
        response.raise_for_status()

        return response.content

    return Path(source).read_bytes()


def sanitize_tvg_id(text):
    """
    Generate IPTV-friendly tvg-id.
    """

    text = text.lower().strip()

    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)

    return text.strip("-")


def get_first_text(parent, xpath):
    """
    Return first matching XML text.
    """

    elem = parent.find(xpath, NAMESPACES)

    if elem is not None and elem.text:
        return elem.text.strip()

    return None


def find_first_name(service, debug=False):
    """
    Locate best DVB-I service/channel name.
    Prioritises ServiceName fields.
    """

    #
    # Preferred tag order
    #
    preferred_tags = [
        "ServiceName",
        "Name",
    ]

    #
    # Search recursively ignoring namespaces
    #
    for preferred in preferred_tags:

        for elem in service.iter():

            tag = elem.tag.split("}")[-1]

            if tag != preferred:
                continue

            if elem.text and elem.text.strip():

                value = elem.text.strip()

                if debug:
                    print(
                        f"[DEBUG] Found {preferred}: "
                        f"{value}"
                    )

                return value

    if debug:
        print("[DEBUG] No ServiceName found")

    return "Unknown Channel"

def build_lcn_table(root, debug=False):
    """
    Build mapping:
        UniqueIdentifier -> channelNumber
    """

    lcn_map = {}

    #
    # Walk XML tree
    #
    for elem in root.iter():

        tag = elem.tag.split("}")[-1]

        #
        # Match only LCN elements
        #
        if tag != "LCN":
            continue

        service_ref = None
        channel_number = None

        #
        # Extract attributes
        #
        for attr_name, attr_value in elem.attrib.items():

            clean_attr = attr_name.split("}")[-1]

            #
            # serviceRef attribute
            #
            if clean_attr == "serviceRef":

                service_ref = attr_value.strip()

            #
            # channelNumber attribute
            #
            elif clean_attr == "channelNumber":

                channel_number = attr_value.strip()

        #
        # Store mapping
        #
        if service_ref and channel_number:

            lcn_map[service_ref] = channel_number

            if debug:
                print(
                    f"[DEBUG] LCN MAP: "
                    f"{service_ref} -> {channel_number}"
                )

    return lcn_map

def find_dash_uri(service, debug=False):
    """
    Locate DASH manifest URI from DVB-I structures.
    Uses namespace-agnostic recursive matching.
    """

    #
    # Walk every element
    #
    for dash in service.iter():

        #
        # Ignore namespace
        #
        tag = dash.tag.split("}")[-1]

        if tag != "DASHDeliveryParameters":
            continue

        if debug:
            print("\n[DEBUG] Found DASHDeliveryParameters")

        #
        # Dump subtree
        #
        if debug:

            for elem in dash.iter():

                elem_tag = elem.tag.split("}")[-1]

                text = (
                    elem.text.strip()
                    if elem.text and elem.text.strip()
                    else ""
                )

                print(f"[DEBUG] TAG={elem_tag} TEXT={text}")

        #
        # Search recursively for URI elements
        #
        for elem in dash.iter():

            elem_tag = elem.tag.split("}")[-1]

            if elem_tag != "URI":
                continue

            if elem.text and elem.text.strip():

                uri = elem.text.strip()

                if debug:
                    print(f"[DEBUG] Found DASH URI: {uri}")

                return uri

    if debug:
        print("[DEBUG] No DASH URI found")

    return None


def extract_services(xml_data, lcn_offset=0, debug=False):
    """
    Extract IPTV-compatible services from DVB-I XML.
    """

    root = ET.fromstring(xml_data)

    #
    # Build global LCN lookup
    #
    lcn_map = build_lcn_table(root, debug=debug)

    services = []

    #
    # Find all Service elements
    #
    for service in root.iter():

        tag = service.tag.split("}")[-1]

        if tag != "Service":
            continue

        #
        # Channel name
        #
        name = find_first_name(service, debug=debug)

        #
        # tvg-id
        #
        service_id = (
            get_first_text(service, ".//dvb:UniqueIdentifier")
            or sanitize_tvg_id(name)
        )

        #
        # Group/category
        #
        group_title = (
            get_first_text(service, ".//dvb:Category")
            or "DVB-I"
        )

        #
        # Logo URI
        #
        logo = (
            get_first_text(service, ".//dvb:MediaUri")
            or ""
        )

        #
        # Service UniqueIdentifier
        #
        unique_id = get_first_text(
            service,
            ".//dvb:UniqueIdentifier"
        )

        #
        # Extract UniqueIdentifier
        #
        unique_id = None

        for elem in service.iter():

            tag = elem.tag.split("}")[-1]

            if tag != "UniqueIdentifier":
                continue

            if elem.text and elem.text.strip():

                unique_id = elem.text.strip()

                break

        #
        # Exact LCN lookup
        #
        lcn = None

        if unique_id:

            lcn = lcn_map.get(unique_id)

            if debug:
                print(
                    f"[DEBUG] Service={name} "
                    f"UniqueIdentifier={unique_id} "
                    f"LCN={lcn}"
                )

        #
        # Apply optional offset
        #
        if lcn is not None:

            try:
                lcn = str(int(lcn) + lcn_offset)

            except ValueError:
                pass

        #
        # DASH URI
        #
        dash_uri = find_dash_uri(service, debug=debug)

        #
        # Skip services without URI
        #
        if not dash_uri:

            if debug:
                print(f"[DEBUG] Skipping service: {name}")

            continue

        #
        # Add service
        #
        services.append({
            "name": name,
            "tvg_id": service_id,
            "group": group_title,
            "logo": logo,
            "lcn": lcn,
            "url": dash_uri,
        })

        if debug:
            print(
                f"[DEBUG] Added service: "
                f"{name} -> {dash_uri}"
            )

    #
    # Sort by channel number
    #
    def lcn_sort_key(service):

        try:
            return int(service["lcn"])

        except (TypeError, ValueError):
            return 999999

    services.sort(key=lcn_sort_key)

    return services


def write_m3u8(services, output_file):
    """
    Write IPTV-compatible M3U8 playlist.
    """

    with open(output_file, "w", encoding="utf-8") as f:

        f.write("#EXTM3U\n")

        for svc in services:

            attrs = [
                f'tvg-id="{svc["tvg_id"]}"',
                f'tvg-name="{svc["name"]}"',
                f'group-title="{svc["group"]}"',
            ]

            #
            # Optional logo
            #
            if svc["logo"]:
                attrs.append(
                    f'tvg-logo="{svc["logo"]}"'
                )

            #
            # Optional channel number
            #
            if svc["lcn"]:
                attrs.append(
                    f'tvg-chno="{svc["lcn"]}"'
                )

            extinf = (
                "#EXTINF:-1 "
                + " ".join(attrs)
                + f',{svc["name"]}'
            )

            f.write(extinf + "\n")
            f.write(svc["url"] + "\n")


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Convert DVB-I Service List "
            "to IPTV-compatible M3U8 playlist"
        )
    )

    parser.add_argument(
        "source",
        help="DVB-I XML URL or filename"
    )

    parser.add_argument(
        "output",
        help="Output M3U8 filename"
    )

    parser.add_argument(
        "--lcn-offset",
        type=int,
        default=0,
        help="Add offset to channel numbers"
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DVB-I parsing debug output"
    )

    args = parser.parse_args()

    try:

        if args.debug:
            print(f"[DEBUG] Loading XML: {args.source}")

        xml_data = load_xml(args.source)

        if args.debug:
            print(
                f"[DEBUG] Loaded XML bytes: "
                f"{len(xml_data)}"
            )

        services = extract_services(
            xml_data,
            lcn_offset=args.lcn_offset,
            debug=args.debug
        )

        if not services:

            print(
                "No DVB-I DASH services found.",
                file=sys.stderr
            )

            sys.exit(2)

        write_m3u8(services, args.output)

        print(
            f"Wrote {len(services)} channels "
            f"to {args.output}"
        )

    except requests.RequestException as e:

        print(
            f"Network error: {e}",
            file=sys.stderr
        )

        sys.exit(1)

    except ET.ParseError as e:

        print(
            f"XML parse error: {e}",
            file=sys.stderr
        )

        sys.exit(1)

    except FileNotFoundError as e:

        print(
            f"File error: {e}",
            file=sys.stderr
        )

        sys.exit(1)

    except Exception as e:

        print(
            f"Error: {e}",
            file=sys.stderr
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
