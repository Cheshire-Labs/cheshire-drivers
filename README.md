# Cheshire Drivers

Shared driver layer for Cheshire Labs lab automation. This package provides driver interfaces, PyLabRobot (PLR) wrappers, and simulation drivers for lab automation equipment.

## Installation

```bash
pip install -e .
```

**Use a dedicated virtual environment.** This installs a pinned commit of
[our PyLabRobot fork](https://github.com/Cheshire-Labs/pylabrobot) under the
name `pylabrobot`, so it replaces an upstream PyLabRobot in the same
environment, and a later `pip install pylabrobot` breaks the Opentrons Flex
imports here. The fork carries the Flex backend, which upstream does not have.

## Usage

```python
from cheshire_drivers import (
    # Interfaces
    BaseDriver,
    IShakerDriver,
    ISealerDriver,
    ICentrifugeDriver,
    ITransporterDriver,
    IReaderDriver,
    IDelidderDriver,
    IStorageDriver,
    IPlateWasherDriver,
    ILiquidHandlerDriver,

    # PLR Wrappers
    PLRShakerBackendWrapper,
    PLRSealerBackendWrapper,
    PLRCentrifugeBackendWrapper,
    PLRTransporterBackendWrapper,

    # Simulation Drivers
    SimShakerDriver,
    SimSealerDriver,
    SimCentrifugeDriver,
    SimTransporterDriver,
    SimStorageDriver,
    SimPlateWasherDriver,
    SimLiquidHandlerDriver,
    SimDelidderDriver,
    SimReaderDriver,

    # Teachpoints
    Teachpoint,
    CartesianCoordinates,
    JointCoordinates,
    TeachpointsRegistry,
    AccessConfig,

    # Specialized Drivers
    VenusProtocolDriver,
    SimulationVenusProtocolDriver,
)
```

## Components

### Interfaces

Abstract base classes defining the contract for each device type:

| Interface | Description |
|-----------|-------------|
| `BaseDriver` | Base class for all drivers |
| `IShakerDriver` | Shaker devices |
| `ISealerDriver` | Plate sealers |
| `ICentrifugeDriver` | Centrifuges |
| `ITransporterDriver` | Robotic arms/transporters |
| `IReaderDriver` | Plate readers |
| `IDelidderDriver` | Delidding devices |
| `IStorageDriver` | Storage devices |
| `IPlateWasherDriver` | Plate washers |
| `ILiquidHandlerDriver` | Liquid handlers |

### PLR Wrappers

Adapters that wrap PyLabRobot backends to implement our interfaces:

| Wrapper | PLR Backend Type |
|---------|------------------|
| `PLRShakerBackendWrapper` | `ShakerBackend` |
| `PLRSealerBackendWrapper` | `SealerBackend` |
| `PLRCentrifugeBackendWrapper` | `CentrifugeBackend` |
| `PLRTransporterBackendWrapper` | `PreciseFlex` (imported as `PLRArmBackend`) |

### Simulation Drivers

Mock implementations for testing without hardware:

| Driver | Description |
|--------|-------------|
| `SimShakerDriver` | Simulated shaker |
| `SimSealerDriver` | Simulated sealer |
| `SimCentrifugeDriver` | Simulated centrifuge |
| `SimTransporterDriver` | Simulated robotic arm |
| `SimStorageDriver` | Simulated storage |
| `SimPlateWasherDriver` | Simulated plate washer |
| `SimLiquidHandlerDriver` | Simulated liquid handler |
| `SimDelidderDriver` | Simulated delidder |
| `SimReaderDriver` | Simulated plate reader |

### Teachpoints

Position management for robotic arms:

| Class | Description |
|-------|-------------|
| `Teachpoint` | Named position with coordinates and access parameters |
| `CartesianCoordinates` | X, Y, Z, yaw, pitch, roll coordinates |
| `JointCoordinates` | Joint-based coordinates |
| `TeachpointsRegistry` | Registry for managing teachpoints |
| `AccessConfig` | Reusable access configuration (vertical/horizontal approach) |

### Specialized Drivers

| Driver | Description |
|--------|-------------|
| `VenusProtocolDriver` | Runs Hamilton Venus protocols |
| `SimulationVenusProtocolDriver` | Simulated Venus driver |
| `NullPlatePadDriver` | Placeholder for locations without active devices |

## Contributing

See [CONTRIBUTING](./CONTRIBUTING) for how contributions reach this repository.

Contributors must sign the [Cheshire Labs Contributor Agreement](https://cla-assistant.io/Cheshire-Labs/cheshire-drivers), which assigns copyright in the contribution to Cheshire Labs.

## License

Source-available under the [Server Side Public License v1 (SSPL-1.0)](LICENSE)
from 1.0.0 onward. Earlier releases were AGPL-3.0.
[NOTICE](./NOTICE) names the copyright holder.

## Acknowledgments

### PyLabRobot

This project wraps and builds upon [PyLabRobot](https://github.com/PyLabRobot/pylabrobot), an open-source, hardware-agnostic interface for liquid-handling robots and accessories. It installs from our fork rather than from upstream; see Installation above for what that means for your environment.

If you use this software in academic research, please cite PyLabRobot:

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

- [Orca](https://github.com/Cheshire-Labs/orca) - Lab automation scheduler using these drivers
