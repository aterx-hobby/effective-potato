FROM ubuntu:24.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Update and install required packages
RUN apt-get update && apt-get install -y \
    build-essential \
    snapd \
    golang-1.23 \
    xorg-dev \
    xserver-xorg-core \
    python3 \
    python3-pip \
    python3-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Note: Snap packages like rustup need snapd service running
# which typically doesn't work well in Docker. We'll install rust via rustup.sh instead
RUN apt-get update && apt-get install -y curl && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Add Go to PATH
ENV PATH="/usr/lib/go-1.23/bin:${PATH}"

# Add Rust to PATH
ENV PATH="/root/.cargo/bin:${PATH}"

# Copy environment file if it exists (will be handled by build context)
COPY environment.sh /tmp/environment.sh 2>/dev/null || true
RUN if [ -f /tmp/environment.sh ]; then \
        cat /tmp/environment.sh >> /root/.profile; \
    fi

# Set working directory
WORKDIR /workspace

# Run sleep infinity to keep container alive
CMD ["sleep", "infinity"]
