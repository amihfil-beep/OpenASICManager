"""Remote ASIC web HTTP routes."""

import config as app_config

from audit.identity import current_audit_actor
from db import get_miner
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from remote_web import (
    REMOTE_WEB_COOKIE_DOMAIN,
    REMOTE_WEB_COOKIE_NAME,
    REMOTE_WEB_TTL,
    remote_web_cookie_scope_valid,
    remote_web_host_for_ip,
    remote_web_make_token,
    remote_web_miner_for_host,
    remote_web_verify_token,
)


def create_remote_web_router(
    log_event,
):
    router = APIRouter()

    @router.get(
        "/remote/{miner_id}"
    )
    def api_remote_web_open(
        miner_id: int,
    ):

        if not app_config.REMOTE_WEB_ENABLED:

            raise HTTPException(
                status_code=503,
                detail=(
                    "Remote ASIC Web is disabled"
                ),
            )


        if not remote_web_cookie_scope_valid():

            raise HTTPException(
                status_code=503,
                detail=(
                    "REMOTE_WEB_COOKIE_DOMAIN "
                    "is not valid for PUBLIC_DOMAIN"
                ),
            )


        actor = current_audit_actor()


        if not actor.startswith("WEB:"):

            raise HTTPException(
                status_code=403,
                detail=(
                    "Remote Web requires "
                    "authenticated HTTPS access"
                ),
            )


        miner = get_miner(
            miner_id
        )


        if not miner:

            raise HTTPException(
                status_code=404,
                detail="Miner not found",
            )


        remote_host = (
            remote_web_host_for_ip(
                miner["ip"]
            )
        )


        if not remote_host:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Miner IP is outside "
                    "Remote Web network"
                ),
            )


        try:

            token = remote_web_make_token(
                actor
            )

        except RuntimeError as exc:

            raise HTTPException(
                status_code=503,
                detail=str(exc),
            )


        log_event(
            source="MANUAL",
            action="REMOTE_WEB_OPEN",
            miner=miner,
            success=True,
            message=(
                "Remote host: "
                +
                remote_host
            ),
        )


        response = RedirectResponse(
            url=(
                "https://"
                +
                remote_host
                +
                "/"
            ),
            status_code=302,
        )


        response.set_cookie(
            key=REMOTE_WEB_COOKIE_NAME,
            value=token,

            max_age=REMOTE_WEB_TTL,

            path="/",

            domain=REMOTE_WEB_COOKIE_DOMAIN,

            secure=True,
            httponly=True,
            samesite="lax",
        )


        return response


    @router.get(
        "/api/remote/authorize"
    )
    def api_remote_web_authorize(
        request: Request,
    ):

        if not app_config.REMOTE_WEB_ENABLED:

            return Response(
                status_code=404
            )


        token = request.cookies.get(
            REMOTE_WEB_COOKIE_NAME,
            "",
        )


        payload = (
            remote_web_verify_token(
                token
            )
        )


        if not payload:

            return Response(
                status_code=401
            )


        remote_host = (
            request.headers.get(
                "x-remote-host",
                "",
            )
        )


        miner = (
            remote_web_miner_for_host(
                remote_host
            )
        )


        if not miner:

            return Response(
                status_code=401
            )


        return Response(
            status_code=204,
            headers={
                "X-Remote-Actor":
                    str(payload["actor"]),
            },
        )


    return router
