"""
Firmware-independent control dispatch.

This module selects the appropriate firmware driver while
keeping firmware transport details outside the main app.
"""

from drivers.bitmain import (
    stock_status,
    stock_control,
    send_stock_reboot,
)

from drivers.awesome import (
    awesome_status,
    awesome_control,
    send_awesome_reboot,
)


__all__ = (
    "read_status",
    "control",
    "send_reboot_command",
)


def read_status(miner):
    driver = miner["driver"]

    if driver == "bitmain_stock":

        return stock_status(
            miner
        )

    if driver == "awesome":

        return awesome_status(
            miner
        )

    raise RuntimeError(
        "ASIC firmware "
        "is not configured"
    )


def control(
    miner,
    action,
):
    driver = miner["driver"]

    if driver == "bitmain_stock":

        return stock_control(
            miner,
            action,
        )

    if driver == "awesome":

        return awesome_control(
            miner,
            action,
        )

    raise RuntimeError(
        "ASIC firmware "
        "is not configured"
    )


def send_reboot_command(
    miner,
):

    driver = miner["driver"]


    if driver == "awesome":

        return send_awesome_reboot(
            miner
        )


    if driver == "bitmain_stock":

        return send_stock_reboot(
            miner
        )


    raise RuntimeError(
        "Reboot unsupported for this firmware"
    )
