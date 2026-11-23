# This file uses a staged build, using a different stage to build the UI (magma)
# Build the UI
FROM node:23 AS ui-build

WORKDIR /usr/src/app

ADD . .
# Build VueJS front-end
RUN (cd plugins/magma; npm install && npm run build)

# This is the runtime stage
# It containes all dependencies required by caldera
FROM debian:bookworm-slim AS runtime

# There are two variants - slim and full
# The slim variant excludes some dependencies of *emu* and *atomic* that can be downloaded on-demand if needed
# They are very large
ARG VARIANT=full
ENV VARIANT=${VARIANT}

# Display an error if variant is set incorrectly, otherwise just print information regarding which variant is in use
RUN if [ "$VARIANT" = "full" ]; then \
        echo "Building \"full\" container suitable for offline use!"; \
    elif [ "$VARIANT" = "slim" ]; then \
        echo "Building slim container - some plugins (emu, atomic) may not be available without an internet connection!"; \
    else \
        echo "Invalid Docker build-arg for VARIANT! Please provide either \"full\" or \"slim\"."; \
        exit 1; \
fi

WORKDIR /usr/src/app

# Copy in source code and compiled UI
# IMPORTANT NOTE: the .dockerignore file is very important in preventing weird issues.
# Especially if caldera was ever compiled outside of Docker - we don't want those files to interfere with this build process,
# which should be repeatable.
ADD . .
COPY --from=ui-build /usr/src/app/plugins/magma/dist /usr/src/app/plugins/magma/dist

# From https://docs.docker.com/build/building/best-practices/
# Install caldera dependencies
RUN apt-get update && \
apt-get --no-install-recommends -y install git curl unzip python3-dev python3-pip mingw-w64 zlib1g gcc && \
rm -rf /var/lib/apt/lists/*

# Install Go from the official release archive (the apt version is too out-of-date).
# The archive is checked against the SHA-256 listed for it on https://go.dev/dl/
# before it is unpacked; update both values together.
ARG GO_VERSION=1.25.0
ARG GO_SHA256=2852af0cb20a13139b3448992e69b868e50ed0f8a1e5940ee1de9e19a123b613
RUN curl -fsSL "https://dl.google.com/go/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tar.gz && \
echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum -c - && \
tar -C /usr/local -xzf /tmp/go.tar.gz && rm /tmp/go.tar.gz
ENV PATH="$PATH:/usr/local/go/bin"
RUN go version

# Fix line ending error that can be caused by cloning the project in a Windows environment
RUN cd /usr/src/app/plugins/sandcat && \
cp ./update-agents.sh ./update-agents_orig.sh && \
tr -d '\15\32' < ./update-agents_orig.sh > ./update-agents.sh

# Set timezone (default to UTC)
ARG TZ="UTC"
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone

# Install pip requirements
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

# Install what the debrief plugin, which conf/default.yml enables, needs on top of
# requirements.txt. The version is pinned so that an image build is repeatable.
# svglib is left out: it is licensed under the LGPL 3.0, which an ASF convenience
# binary must not include. Without it, debrief builds PDF reports with a note in
# place of each graph.
RUN pip3 install --break-system-packages --no-cache-dir --only-binary :all: lxml==6.1.3

# Stop the build if the packages installed above no longer meet the plugin's own
# requirements, svglib aside. --dry-run and --no-index mean nothing is installed.
RUN grep -v '^svglib' plugins/debrief/requirements.txt > /tmp/debrief-requirements.txt && \
pip3 install --break-system-packages --dry-run --no-index --only-binary :all: -r /tmp/debrief-requirements.txt && \
rm /tmp/debrief-requirements.txt

# For offline atomic (disable it by default in slim image)
# Disable atomic if this is not downloaded
RUN if [ ! -d "/usr/src/app/plugins/atomic/data/atomic-red-team" ] && [ "$VARIANT" = "full" ]; then   \
        git clone --depth 1 https://github.com/redcanaryco/atomic-red-team.git \
            /usr/src/app/plugins/atomic/data/atomic-red-team;                  \
    else \
        sed -i '/\- atomic/d' conf/default.yml; \
fi

# For offline emu
# (Emu is disabled by default, no need to disable it if slim variant is being built)
RUN if [ ! -d "/usr/src/app/plugins/emu/data/adversary-emulation-plans" ] && [ "$VARIANT" = "full" ]; then   \
        git clone --depth 1 https://github.com/center-for-threat-informed-defense/adversary_emulation_library \
            /usr/src/app/plugins/emu/data/adversary-emulation-plans;                  \
fi

# Download emu payloads
# emu doesn't seem capable of running this itself - always download
RUN cd /usr/src/app/plugins/emu; ./download_payloads.sh

# The commands above (git clone) will generate *huge* .git folders - remove them
RUN (find . -type d -name ".git") | xargs rm -rf

# Install Go dependencies
RUN cd /usr/src/app/plugins/sandcat/gocat; go mod tidy && go mod download

# Update sandcat agents
RUN cd /usr/src/app/plugins/sandcat; ./update-agents.sh

# Make sure emu can always be used in container (even if not enabled right now)
RUN cd /usr/src/app/plugins/emu; \
    pip3 install --break-system-packages -r requirements.txt

STOPSIGNAL SIGINT

# Default HTTP port for web interface and agent beacons over HTTP
EXPOSE 8888

# Default HTTPS port for web interface and agent beacons over HTTPS (requires SSL plugin to be enabled)
EXPOSE 8443

# TCP and UDP contact ports
EXPOSE 7010
EXPOSE 7011/udp

# Websocket contact port
EXPOSE 7012

# Default port to listen for DNS requests for DNS tunneling C2 channel
EXPOSE 8853

# Default port to listen for SSH tunneling requests
EXPOSE 8022

# Default FTP port for FTP C2 channel
EXPOSE 2222

ENTRYPOINT ["python3", "server.py"]
