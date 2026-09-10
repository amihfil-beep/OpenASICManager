"""Audit identity and authenticated user-switch HTTP routes."""

from audit.identity import (
    current_audit_actor,
    sanitize_audit_username,
)
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from remote_web import remote_web_clear_cookie


def create_audit_auth_router(
    log_event,
):
    router = APIRouter()

    @router.get(
        "/api/audit/whoami"
    )
    def api_audit_whoami():

        return {
            "actor":
                current_audit_actor(),
        }


    @router.post(
        "/api/audit/test"
    )
    def api_audit_test():

        actor = (
            current_audit_actor()
        )


        log_event(
            source="MANUAL",
            action="AUDIT_TEST",
            success=True,
            message=(
                "Audit identity test"
            ),
        )


        return {
            "success":
                True,

            "actor":
                actor,
        }


    @router.get(
        "/api/auth/relogin"
    )
    def api_auth_relogin(
        from_user: str = "",
    ):

        actor = (
            current_audit_actor()
        )


        # Direct localhost / PuTTY access
        # does not use nginx Basic Auth.

        if not actor.startswith(
            "WEB:"
        ):

            return HTMLResponse(
                content=(
                    "User switching is only "
                    "available through HTTPS."
                ),
                status_code=400,
                headers={
                    "Cache-Control":
                        "no-store",
                },
            )


        current_user = (
            actor[
                len("WEB:"):
            ]
        )


        previous_user = (
            sanitize_audit_username(
                from_user
            )
        )


        if not previous_user:

            return HTMLResponse(
                content="Missing current user.",
                status_code=400,
                headers={
                    "Cache-Control":
                        "no-store",
                },
            )


        # The currently cached Basic Auth
        # credentials are intentionally rejected.
        #
        # Browser receives a Basic challenge
        # for the same nginx realm and asks
        # for credentials again.

        if (
            current_user
            ==
            previous_user
        ):

            response = Response(
                status_code=401,
                headers={
                    "WWW-Authenticate":
                        'Basic realm="OpenASICManager"',

                    "Cache-Control":
                        (
                            "no-store, no-cache, "
                            "must-revalidate"
                        ),

                    "Pragma":
                        "no-cache",
                },
            )


            remote_web_clear_cookie(
                response
            )


            return response


        # We reached this point after the browser
        # supplied another valid Basic Auth account.

        log_event(
            source="MANUAL",
            action="USER_SWITCH",
            success=True,
            message=(
                "Previous user: WEB:"
                +
                previous_user
            ),
        )


        response = RedirectResponse(
            url="/",
            status_code=302,
            headers={
                "Cache-Control":
                    "no-store",
            },
        )


        remote_web_clear_cookie(
            response
        )


        return response


    return router
