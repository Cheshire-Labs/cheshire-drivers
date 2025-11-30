# Cheshire Drivers

Shared driver layer for Cheshire Labs lab automation. This package provides driver interfaces, PyLabRobot (PLR) wrappers, and simulation drivers for lab automation equipment.

## Installation

```bash
pip install -e .
```

## Usage

```python
from cheshire_drivers import (
    # Interfaces
    IShakerDriver,
    ITransporterDriver,
    ICentrifugeDriver,

    # PLR Wrappers
    PLRShakerBackendWrapper,
    PLRTransporterBackendWrapper,

    # Simulation Drivers
    SimShakerDriver,
    SimTransporterDriver,

    # Teachpoints
    Teachpoint,
    TeachpointsRegistry,
)
```

## Components

### Interfaces
Abstract base classes defining the contract for each device type:
- `IShakerDriver` - Shaker devices
- `ISealerDriver` - Plate sealers
- `ICentrifugeDriver` - Centrifuges
- `ITransporterDriver` - Robotic arms/transporters
- And more...

### PLR Wrappers
Adapters that wrap PyLabRobot backends to implement our interfaces:
- `PLRShakerBackendWrapper`
- `PLRSealerBackendWrapper`
- `PLRCentrifugeBackendWrapper`
- `PLRTransporterBackendWrapper`

### Simulation Drivers
Mock implementations for testing:
- `SimShakerDriver`
- `SimSealerDriver`
- `SimCentrifugeDriver`
- `SimTransporterDriver`
- And more...

### Teachpoints
Position management for robotic arms:
- `Teachpoint` - Named position with coordinates and access parameters
- `TeachpointsRegistry` - Registry for managing teachpoints
- `AccessConfig` - Reusable access configuration

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0-only)**.

See the [LICENSE](LICENSE) file for the full license text.

## Acknowledgments

### PyLabRobot

This project wraps and builds upon [PyLabRobot](https://github.com/PyLabRobot/pylabrobot), an open-source, hardware-agnostic interface for liquid-handling robots and accessories. PyLabRobot was developed for the Sculpting Evolution Group at the MIT Media Lab.

If you use this software in academic research, please cite PyLabRobot:

> Wierenga, R.P., Golas, S.M., Ho, W., Coley, C.W., & Esvelt, K.M. (2023). PyLabRobot: An open-source, hardware-agnostic interface for liquid-handling robots and accessories. *Device*, 1(4), 100111. https://doi.org/10.1016/j.device.2023.100111

```bibtex
@article{wierenga2023pylabrobot,
  title={PyLabRobot: An open-source, hardware-agnostic interface for liquid-handling robots and accessories},
  author={Wierenga, Rick P. and Golas, Stefan M. and Ho, Wilson and Coley, Connor W. and Esvelt, Kevin M.},
  journal={Device},
  volume={1},
  number={4},
  pages={100111},
  year={2023},
  publisher={Elsevier},
  doi={10.1016/j.device.2023.100111}
}
```

## Related Projects

- [Orca Client Driver](https://github.com/Cheshire-Labs/orca-client-driver) - On-premise WebSocket client using these drivers
- [Swarm Backend](https://github.com/Cheshire-Labs/swarm) - Cloud gateway for device integration
