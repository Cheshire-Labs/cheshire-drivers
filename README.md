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
