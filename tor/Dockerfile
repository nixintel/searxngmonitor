# Tor SOCKS proxy used by the searxng-tor instance (ahmia / torch .onion searches).
# Off by default: the web app writes torrc with "DisableNetwork 1" until Tor is turned on in Settings -> Tor.
FROM alpine:3.20

RUN apk add --no-cache tor

COPY entrypoint.sh /entrypoint.sh
# The sed strips Windows line endings, in case the project was copied or zipped on Windows.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

USER tor
ENTRYPOINT ["/entrypoint.sh"]
