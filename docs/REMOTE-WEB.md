# Remote ASIC Web

Remote ASIC Web is an optional OpenASICManager feature.

It provides access to the original ASIC firmware web interface through the
OpenASICManager nginx host rather than exposing every ASIC directly to the
Internet.

The feature is disabled by default.

## Access flow

The user first authenticates to the main OpenASICManager HTTPS interface.

When the user presses Open for an ASIC:

    OpenASICManager
          |
          | creates signed short-lived token
          |
          v
    Browser receives secure cookie
          |
          v
    Browser is redirected to ASIC hostname
          |
          v
    nginx auth_request
          |
          v
    /api/remote/authorize
          |
          +---- authorized -> ASIC web UI
          |
          +---- rejected -> main manager URL

## Hostname format

The complete IPv4 address is encoded in the hostname.

Example:

    ASIC IP:
        192.168.1.81

    Remote hostname:
        m192-168-1-81.manager.example.com

For another subnet:

    ASIC IP:
        10.20.30.40

    Remote hostname:
        m10-20-30-40.manager.example.com

This prevents collisions between ASICs on different networks.

## DNS hierarchy

The main OpenASICManager hostname and Remote ASIC Web hostnames must use a
compatible browser cookie scope.

Recommended configuration:

    Main manager:
        manager.example.com

    Remote ASICs:
        m192-168-1-81.manager.example.com
        m192-168-1-82.manager.example.com

    Cookie domain:
        .manager.example.com

Using an unrelated Remote Web domain is not supported because a page at
manager.example.com cannot set a browser cookie for an unrelated DNS tree.

## DNS records

The simplest deployment uses wildcard DNS:

    *.manager.example.com

pointing to the OpenASICManager nginx server.

Individual DNS records for generated hostnames can also be used.

## Environment configuration

Example:

    REMOTE_WEB_ENABLED=true

    PUBLIC_DOMAIN=manager.example.com

    REMOTE_WEB_BASE_DOMAIN=manager.example.com
    REMOTE_WEB_COOKIE_DOMAIN=.manager.example.com

    REMOTE_WEB_ALLOWED_CIDR=192.168.1.0/24

    REMOTE_WEB_SECRET=<random-secret>

    REMOTE_WEB_CERT=/path/to/fullchain.pem
    REMOTE_WEB_KEY=/path/to/privkey.pem

## Remote Web secret

Use cryptographically secure random data.

Example:

    openssl rand -hex 32

Store the generated value as:

    REMOTE_WEB_SECRET=

in:

    /etc/openasicmanager/openasicmanager.env

Never commit the real value to Git.

## TLS certificate

The certificate used for Remote ASIC Web must cover all generated hostnames.

The simplest option is a wildcard certificate such as:

    *.manager.example.com

A SAN certificate containing every generated ASIC hostname is also possible.

The main manager certificate for:

    manager.example.com

does not automatically cover:

    *.manager.example.com

unless the certificate explicitly includes the wildcard name.

## Generate nginx configuration

Preview generated configuration:

    sudo -E \
        /opt/openasicmanager/scripts/generate-remote-nginx \
        --stdout

Apply generated configuration:

    sudo -E \
        /opt/openasicmanager/scripts/generate-remote-nginx \
        --apply

The generator:

    - reads managed ASICs from SQLite;
    - includes supported managed ASICs;
    - checks REMOTE_WEB_ALLOWED_CIDR;
    - generates hostname-specific nginx blocks;
    - preserves Bitmain Digest Authorization;
    - validates configuration with nginx -t;
    - enables the generated nginx site;
    - reloads nginx only after successful validation.

## Bitmain Digest authentication

Bitmain Stock firmware may use HTTP Digest authentication.

The browser Authorization header must therefore be forwarded to the ASIC:

    Authorization $http_authorization

However, ASIC credentials must not be sent to OpenASICManager's internal
authorization endpoint.

The nginx auth_request location explicitly removes Authorization before
calling:

    /api/remote/authorize

## Adding a new ASIC

After adding a new ASIC, regenerate the nginx configuration:

    sudo -E \
        /opt/openasicmanager/scripts/generate-remote-nginx \
        --apply

## Removing an ASIC

After removing an ASIC from management, regenerate the configuration again so
that the obsolete Remote Web virtual host is removed.

## Security notes

Remote ASIC Web does not make the ASIC firmware itself more secure.

It reduces direct exposure by placing nginx and OpenASICManager authorization
in front of the device.

ASIC firmware credentials are still required when the firmware requests them.

Use HTTPS and strong ASIC administrator passwords.
