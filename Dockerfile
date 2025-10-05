FROM effective-potato-base:latest

ARG INSTALL_GUI=1

# Install Rust via rustup
RUN apt-get update && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup.sh && \
    chmod +x /tmp/rustup.sh && \
    /tmp/rustup.sh -y --default-toolchain stable && \
    rm /tmp/rustup.sh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI
RUN apt-get update && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && \
    apt-get install -y gh && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Add Go and Rust to PATH
ENV PATH="/usr/lib/go-1.23/bin:${PATH}"
ENV PATH="/root/.cargo/bin:${PATH}"

# Set working directory
WORKDIR /workspace

# Default display for X servers
ENV DISPLAY=:0

# Copy runtime scripts and supervisor config (only if GUI installed)
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
COPY scripts/xserver-entry.sh /usr/local/bin/xserver-entry.sh
RUN chmod +x /usr/local/bin/xserver-entry.sh && mkdir -p /var/log/supervisor
COPY scripts/supervisor/xserver.conf /etc/supervisor/conf.d/xserver.conf

# Ensure non-root user exists and owns workspace
RUN id -u ubuntu >/dev/null 2>&1 || useradd -ms /bin/bash ubuntu && chown -R ubuntu:ubuntu /workspace

# Use entrypoint to start supervisord and then exec main command as ubuntu
ENV POTATO_GUI=1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]