FROM python:3.9 AS pipenv
WORKDIR /app
COPY Pipfile .
RUN pip install --no-cache-dir pipenv && \
    pipenv lock -r >requirements.txt

FROM python:3.9
WORKDIR /app
COPY --from=pipenv /app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
VOLUME /etc/ndn-quic-gateway
CMD python main.py \
      --cert /etc/ndn-quic-gateway/tls.cert --key /etc/ndn-quic-gateway/tls.key \
      --listen-addr "${ADDR:-::}" --listen-port "${PORT:-6367}" \
      --router-addr "${ROUTER}"
