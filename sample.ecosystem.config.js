const ROUTER = "ndn-router.example.net";

module.exports = {
  apps: [
    {
      name: "NDN-QUIC-gateway",
      interpreter: "pipenv",
      interpreter_args: "run python",
      script: "main.py",
      args: `--cert .data/tls.cert --key .data/tls.key --listen-addr 0.0.0.0 --router-addr ${ROUTER}`,
    },
  ],
};
