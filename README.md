[![Release](https://img.shields.io/badge/dynamic/json?color=blue&label=Release&query=tag_name&url=https%3A%2F%2Fapi.github.com%2Frepos%2Fapache%2Fcaldera%2Freleases%2Flatest)](https://github.com/apache/caldera/releases/latest)
[![Testing Status](https://github.com/apache/caldera/actions/workflows/quality.yml/badge.svg?branch=master)](https://github.com/apache/caldera/actions/workflows/quality.yml?query=branch%3Amaster)
[![Security Status](https://github.com/apache/caldera/actions/workflows/security.yml/badge.svg?branch=master)](https://github.com/apache/caldera/actions/workflows/security.yml?query=branch%3Amaster)
[![codecov](https://codecov.io/gh/apache/caldera/branch/master/graph/badge.svg)](https://codecov.io/gh/apache/caldera)
[![Documentation Status](https://readthedocs.org/projects/caldera/badge/?version=stable)](http://caldera.readthedocs.io/?badge=stable)

# Caldera&trade;

Caldera&trade; is a cyber security platform designed to easily automate adversary emulation, assist manual red-teams, and automate incident response.

It is built on the [MITRE ATT&CK™ framework](https://attack.mitre.org/) and is an active research project at MITRE.

The framework consists of two components:

1) **The core system**. This is the framework code, consisting of what is available in this repository. Included is
an asynchronous command-and-control (C2) server with a REST API and a web interface.
2) **Plugins**. These repositories expand the core framework capabilities and providing additional functionality. Examples include agents, reporting, collections of TTPs and more.

## Resources & Socials
* 📜 [Documentation, training, and use-cases](https://caldera.readthedocs.io/en/latest/)
* 🎬 [Tutorial Videos](https://www.youtube.com/playlist?list=PLF2bj1pw7-ZvLTjIwSaTXNLN2D2yx-wXH)
* ✍️ [Caldera's blog](https://medium.com/@apachecaldera/welcome-to-the-official-apache-caldera-blog-page-f34c2cdfef09)
* 🌐 [Homepage](https://caldera.apache.org)


## Plugins

:star: Create your own plugin! Plugin generator: **[Skeleton](https://github.com/mitre/skeleton)** :star:

### Default
These plugins are supported and maintained by the Caldera team.
- **[Access](https://github.com/mitre/access)** (red team initial access tools and techniques)
- **[Atomic](https://github.com/mitre/atomic)** (Atomic Red Team project TTPs)
- **[Builder](https://github.com/mitre/builder)** (dynamically compile payloads)
- **[Caldera for OT](https://github.com/mitre/caldera-ot)** (ICS/OT capabilities for Caldera)
- **[Compass](https://github.com/mitre/compass)** (ATT&CK visualizations)
- **[Debrief](https://github.com/mitre/debrief)** (operations insights)
- **[Emu](https://github.com/mitre/emu)** (CTID emulation plans)
- **[Fieldmanual](https://github.com/mitre/fieldmanual)** (documentation)
- **[GameBoard](https://github.com/mitre/gameboard)** (visualize joint red and blue operations)
- **[Human](https://github.com/mitre/human)** (create simulated noise on an endpoint)
- **[Magma](https://github.com/mitre/magma)** (VueJS UI for Caldera v5)
- **[Manx](https://github.com/mitre/manx)** (shell functionality and reverse shell payloads)
- **[Response](https://github.com/mitre/response)** (incident response)
- **[Sandcat](https://github.com/mitre/sandcat)** (default agent)
- **[SSL](https://github.com/mitre/SSL)** (enable https for caldera)
- **[Stockpile](https://github.com/mitre/stockpile)** (technique and profile storehouse)
- **[Training](https://github.com/mitre/training)** (certification and training course)

### More
These plugins are ready to use but are not included by default and are not maintained by the Caldera team.
- **[Arsenal](https://github.com/mitre-atlas/arsenal)** (MITRE ATLAS techniques and profiles)
- **[BountyHunter](https://github.com/fkie-cad/bountyhunter)** (The Bounty Hunter)
- **[CalTack](https://github.com/mitre/caltack.git)** (embedded ATT&CK website)
- **[SAML](https://github.com/mitre/saml)** (SAML authentication)

## Requirements

These requirements are for the computer running the core framework:

* Any Linux or MacOS
  * Python 3.10+ (with Pip3)
* Recommended hardware to run on is 8GB+ RAM and 2+ CPUs
* Recommended: [GoLang 1.24+](https://go.dev/doc/install) to dynamically compile GoLang-based agents.
* NodeJS (v16+ recommended for v5 VueJS UI)

## Installation

Note: we HIGHLY recommend installing Caldera in a Python virtual environment to avoid issues with pip packages. You can use an existing environment if you wish or create a new one from scratch:
```Bash
python3 -m venv .calderavenv
source .calderavenv/bin/activate
```

Concise installation steps:
```Bash
git clone https://github.com/apache/caldera.git --recursive
cd caldera
pip3 install -r requirements.txt
python3 server.py --insecure --build
```

Full steps:
Start by cloning this repository recursively, passing the desired version/release in x.x.x format. This will pull in all available plugins.
```Bash
git clone https://github.com/apache/caldera.git --recursive --tag x.x.x
```

Next, install the PIP requirements:
```Bash
pip3 install -r requirements.txt
```

Finally, start the server.
```Bash
python3 server.py --insecure --build
```

The `--build` flag automatically installs any VueJS UI dependencies, bundles the UI into a dist directory and is served by the Caldera server. You will only have to use the `--build` flag again if you add any plugins or make any changes to the UI. Once started, log into http://localhost:8888 using the default credentials red/admin. Then go into Plugins -> Training and complete the capture-the-flag style training course to learn how to use Caldera.

If you prefer to not use the new VueJS UI, revert to Caldera v4.2.0. Correspondingly, do not use the `--build` flag for earlier versions as not required.

**Additionally, please note [security recommendations](#Security) for deploying Caldera.**

## Docker Installation

Local build:
```sh
git clone https://github.com/apache/caldera.git --recursive
cd caldera
docker build --build-arg VARIANT=full -t caldera .
docker run -it -p 8888:8888 caldera
```

Adjust the port forwarding (`-p`) and build args (`--build-arg`) as desired to make ports accessible or change the Caldera variant. The ports that you expose depend on which contacts you plan on using (see `Dockerfile` and `docker-compose.yml` for reference).

Pre-Built Image (from GitHub Container Registry):
```sh
docker run -p 8888:8888 ghcr.io/apache/caldera:latest
```
This container may be slightly outdated, we recommend building the container yourself.

To gracefully terminate your docker container, do the following:
```sh
# Find the container ID for your docker container running Caldera
docker ps

# Stop the container
docker stop <container ID>
```

There are two variants available, *full* and *slim*. The *slim* variant doesn't include files necessary for the `emu` and `atomic` plugins, which will be downloaded on-demand if the plugins are ever enabled. The *full* variant is suitable for operation in environments without an internet connection. Slim images on GHCR are prefixed with "slim".

**Docker Container Notes**
- The Caldera container will automatically generate keys/usernames/password on first start.
- If you wish to override the default configuration or avoid automatically generated keys/passwords, consider bind-mounting your own configuration file with the `-v <your_path>/conf.yml:/usr/src/app/conf/local.yml` flag.
- Data stored by Caldera is ephemeral by default. If you wish to make it persistent, use docker volumes and/or bind mounts (`-v <path_to_your_data_or_volume_name>:/usr/src/app/data/`). Ensure that the directory structure is the same as in the `data/` directory on GitHub, as Caldera will refuse to create these sub-directories if they are missing. Lastly, make sure that the configuration file is also made persistent to prevent issues with encryption keys.
- The `builder` plugin will not work within Docker.
- If you wish to modify data used by the `atomic` plugin, clone the `Atomic Red Team` repository outside the container, apply your modifications and bind-mount it (`-v`) to `/usr/src/app/plugins/atomic/data/atomic-red-team` within the container.
- If you wish to modify data used by `emu`, clone the `adversary_emulation_library` repository locally and bind-mount it (`-v`) to `/usr/src/app/plugins/emu/data/adversary-emulation-plans`.

**Additionally, please note [security recommendations](#Security) for deploying Caldera.**

### User Interface Development

If you'll be developing the UI, there are a few more additional installation steps.

**Requirements**  
* NodeJS (v16+ recommended)

**Setup**

1. Add the Magma submodule if you haven't already: `git submodule add https://github.com/mitre/magma`
1. Install NodeJS dependencies: `cd plugins/magma && npm install && cd ..`
1. Start the Caldera server with an additional flag: `python3 server.py --uidev localhost`

Your Caldera server is available at http://localhost:8888 as usual, but there will now be a hot-reloading development server for the VueJS front-end available at http://localhost:3000. Both logs from the server and the front-end will display in the terminal you launched the server from.

## Security

The Caldera team highly reccommends standing up the Caldera server on a secure environment/network, and not exposing it to the internet. The Caldera server does not have a hardened and thoroughly pentested web application interface, but only basic authentication and security features. Both MITRE and MITRE's US Government sponsors nearly exclusively only use Caldera on secure environments and do not rely on Caldera's own security protocols for proper cyber security.

### Vulnerability Disclosures

Refer to our [Vulnerability Disclosure Documentation](SECURITY.md) for submitting bugs.

#### Recent Vulnerability Disclosures

`🚨Security Notice🚨`: (17 Feb 2025 10:00 EST) Please pull v5.1.0+ for a recent security patch for [CVE-2025-27364](https://www.cve.org/CVERecord?id=CVE-2025-27364). Please update your Caldera instance, especially if you host Caldera on a publicly accessible network. [Vulnerability walkthrough.](https://medium.com/@apachecaldera/mitre-caldera-security-advisory-remote-code-execution-cve-2025-27364-5f679e2e2a0e)

## Contributing

Refer to our [contributor documentation](CONTRIBUTING.md).

## Caldera Benefactor Program

If you are interested in partnering to support, sustain, and evolve MITRE Caldera&trade;'s open source capabilities, please contact us at caldera@mitre.org.
