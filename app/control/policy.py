"""
Control policy.

Contains firmware-independent target-state, timeout and
retry decisions used by the verified control pipeline.
"""


# Awesome / AnthillOS
CONTROL_AWESOME_RETRY_AFTER = 30
CONTROL_AWESOME_PAUSE_TIMEOUT = 90
CONTROL_AWESOME_RESUME_TIMEOUT = 180


# Bitmain Stock
#
# Stock T21 can spend more than 3 minutes in STARTING
# before real hashrate appears.
CONTROL_STOCK_RETRY_AFTER = 60
CONTROL_STOCK_PAUSE_TIMEOUT = 180
CONTROL_STOCK_RESUME_TIMEOUT = 360


__all__ = (
    "control_target",
    "control_timeout",
    "control_retry_after",
    "should_retry_command",
)


def control_target(action):

    if action == "pause":
        return "PAUSED"

    if action == "resume":
        return "MINING"

    raise RuntimeError(
        "Invalid control action"
    )


def control_timeout(
    action,
    miner,
):

    driver = miner["driver"]


    if driver == "bitmain_stock":

        if action == "pause":
            return CONTROL_STOCK_PAUSE_TIMEOUT

        if action == "resume":
            return CONTROL_STOCK_RESUME_TIMEOUT


    if driver == "awesome":

        if action == "pause":
            return CONTROL_AWESOME_PAUSE_TIMEOUT

        if action == "resume":
            return CONTROL_AWESOME_RESUME_TIMEOUT


    raise RuntimeError(
        "Invalid control action or driver"
    )


def control_retry_after(miner):

    if (
        miner["driver"]
        == "bitmain_stock"
    ):

        return (
            CONTROL_STOCK_RETRY_AFTER
        )

    return (
        CONTROL_AWESOME_RETRY_AFTER
    )


def should_retry_command(
    action,
    state,
):

    if action == "pause":

        return state in (
            "MINING",
            "STARTING",
            "IDLE",
            "UNKNOWN",
        )

    if action == "resume":

        # STARTING — нормальное состояние разгона.
        # Повторный Resume в этот момент не посылаем.
        return state in (
            "PAUSED",
            "IDLE",
            "UNKNOWN",
        )

    return False
