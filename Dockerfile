# Prepare the base environment.
FROM python:3.10.12-slim-bookworm as builder_base_healthcheck
MAINTAINER asi@dbca.wa.gov.au
LABEL org.opencontainers.image.source https://github.com/dbca-wa/healthcheck

RUN apt-get update -y \
  && apt-get upgrade -y \
  && rm -rf /var/lib/apt/lists/* \
  && pip install --upgrade pip

# Install Python libs using Poetry.
FROM builder_base_healthcheck as python_libs_healthcheck
WORKDIR /app
ARG POETRY_VERSION=1.6.1
RUN pip install poetry=="${POETRY_VERSION}"
COPY poetry.lock pyproject.toml ./
RUN poetry config virtualenvs.create false \
  && poetry install --no-interaction --no-ansi --only main


# Install a non-root user.
ARG UID=10001
ARG GID=10001
RUN groupadd -g "${GID}" appuser \
  && useradd --no-create-home --no-log-init --uid "${UID}" --gid "${GID}" appuser

# Install the project.
FROM python_libs_healthcheck
COPY status.py ./
COPY static ./static

USER ${UID}
EXPOSE 8080
CMD ["python", "status.py"]
