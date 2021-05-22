FROM python:3.9 AS pipenv
WORKDIR /app
COPY Pipfile .
RUN pip install --no-cache-dir pipenv && \
    pipenv lock -r >requirements.txt

FROM python:3.9
WORKDIR /app
COPY --from=pipenv /app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY entrypoint.sh *.py ./
RUN chmod +x entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
