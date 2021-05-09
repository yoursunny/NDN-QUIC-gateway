# NDN-QUIC gateway

This program bridges between Chromium [WebTransport API](https://web.dev/webtransport/) and Named Data Networking's plain UDP transport.
It is designed to work with [NDNts](https://yoursunny.com/p/NDNts/) `@ndn/quic-transport` package.

## Deployment Instructions

1. Install system-wide dependencies in a sudoer user:

    ```bash
    sudo apt install python3-dev python3-venv
    curl -sL https://bootstrap.pypa.io/get-pip.py | sudo python3
    sudo pip install -U pip pipenv
    ```

2. Clone the repository in a non-root non-sudoer user.

3. Install local dependencies:

    ```bash
    pipenv install
    ```

4. Configure [pm2](https://pm2.keymetrics.io/) service:

    ```bash
    mkdir .data
    touch .data/tls.cert .data/tls.key
    cp sample.ecosystem.config.js ecosystem.config.js
    # modify ecosystem.config.js: fill in ROUTER hostname
    pm2 start ecosystem.config.js
    pm2 stop ecosystem.config.js
    ```

5. Obtain certificate with [acme.sh](https://github.com/acmesh-official/acme.sh) in the root user:
   (assume the service is installed in `node` user)

    ```bash
    acme.sh --issue --standalone -d ndn-quic-gateway.example.net
    acme.sh --install-cert -d ndn-quic-gateway.example.net \
            --fullchain-file '/home/node/NDN-QUIC-gateway/.data/tls.cert' \
            --key-file '/home/node/NDN-QUIC-gateway/.data/tls.key' \
            --reloadcmd 'sudo -u node bash -ic "pm2 restart NDN-QUIC-gateway"'
    ```

6. Edit UDP MTU in NFD configuration:

    ```bash
    infoedit -f /etc/ndn/nfd.conf -s face_system.udp.unicast_mtu -v 1200
    ```
