import ipaddress


def remote_host_for_ip(
    ip_value,
    allowed_network,
    base_domain,
):
    try:

        address = ipaddress.ip_address(
            str(ip_value)
        )

    except ValueError:
        return None


    if address.version != 4:
        return None


    if isinstance(
        allowed_network,
        str,
    ):

        try:

            allowed_network = (
                ipaddress.ip_network(
                    allowed_network,
                    strict=False,
                )
            )

        except ValueError:
            return None


    if address not in allowed_network:
        return None


    domain = str(
        base_domain
        or ""
    ).strip().lower().strip(".")


    if not domain:
        return None


    encoded_ip = (
        str(address)
        .replace(
            ".",
            "-",
        )
    )


    return (
        "m"
        +
        encoded_ip
        +
        "."
        +
        domain
    )
