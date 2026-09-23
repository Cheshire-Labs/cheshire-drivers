"""Pre-built labware factories wrapping the full PLR catalog.

Each entry wraps a PLR factory function so it returns IPlate/ITipRack/ITrough
instead of raw PLR types. Import from here instead of pylabrobot directly.

For PLR labware not listed here, use the escape hatch:
    from cheshire_drivers.plr.labware import plr_plate_factory
    MyPlate = plr_plate_factory(some_plr_factory)
"""

from cheshire_drivers.plr.labware import plr_plate_factory, plr_tip_rack_factory, plr_trough_factory

__all__ = [
    "AGenBio_1_troughplate_100000uL_Fl",
    "axygen_1_reservoir_90ml",
    "nest_1_reservoir_195ml",
    "agilent_1_reservoir_290ml",
    "AGenBio_1_troughplate_190000uL_Fl",
    "AGenBio_1_wellplate_Fl",
    "AGenBio_4_troughplate_75000_Vb",
    "AGenBio_4_troughplate_75000uL_Vb",
    "AGenBio_96_wellplate_Ub_2200ul",
    "Azenta4titudeFrameStar_96_wellplate_200ul_Vb",
    "BioER_96_wellplate_Vb_2200uL",
    "BioRad_384_wellplate_50uL_Vb",
    "CellTreat_12_troughplate_15000ul_Vb",
    "CellTreat_24_wellplate_3300ul_Fb",
    "CellTreat_6_wellplate_16300ul_Fb",
    "CellTreat_96_wellplate_350ul_Fb",
    "CellTreat_96_wellplate_350ul_Ub",
    "CellVis_24_wellplate_3600uL_Fb",
    "CellVis_96_wellplate_350uL_Fb",
    "Cor_96_wellplate_2mL_Vb",
    "Cor_96_wellplate_360ul_Fb",
    "Cor_Axy_24_wellplate_10mL_Vb",
    "Cor_Cos_12_wellplate_6900ul_Fb",
    "Cor_Cos_24_wellplate_3470ul_Fb",
    "Cor_Cos_48_wellplate_1620ul_Fb",
    "Cor_Cos_6_wellplate_16800ul_Fb",
    "Cor_Falcon_96_wellplate_250ul_Rb",
    "Cor_Falcon_96_wellplate_275ul_Fb",
    "Cor_Falcon_96_wellplate_340ul_Fb_Black",
    "Eppendorf_96_wellplate_250ul_Vb",
    "Greiner_384_wellplate_28ul_Fb",
    "HT",
    "HTF",
    "LT",
    "LTF",
    "PerkinElmer_96_wellplate_400ul_Fb",
    "Porvair_24_wellplate_Vb",
    "Porvair_6_reservoir_47ml_Vb",
    "Revvity_384_wellplate_28ul_Ub",
    "ST",
    "STF",
    "STF_Slim",
    "TIP_50ul",
    "TIP_50ul_w_filter",
    "ThermoFisherMatrixTrough8094",
    "Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate",
    "Thermo_Nunc_96_well_plate_1300uL_Rb",
    "Thermo_TS_96_wellplate_1200ul_Rb",
    "VWRReagentReservoirs25mL",
    "VWR_1_troughplate_195000uL_Ub",
    "VWR_96_wellplate_2mL_Vb",
    "agilent_96_wellplate_150uL_Ub",
    "agilent_96_wellplate_150uL_Vb",
    "corning_96_wellplate_360ul_flat",
    "eppendorf_96_tiprack_1000ul_eptips",
    "eppendorf_96_tiprack_10ul_eptips",
    "flex_96_filtertiprack_50ul",
    "flex_96_tiprack_1000ul",
    "flex_96_tiprack_200ul",
    "flex_96_tiprack_50ul",
    "geb_96_tiprack_1000ul",
    "geb_96_tiprack_10ul",
    "hamilton_1_trough_200ml_Vb",
    "hamilton_1_trough_60ml_Vb",
    "hamilton_24_tiprack_4000uL_filter",
    "hamilton_24_tiprack_5000uL",
    "hamilton_96_tiprack_1000uL",
    "hamilton_96_tiprack_1000uL_filter",
    "hamilton_96_tiprack_1000uL_filter_ultrawide",
    "hamilton_96_tiprack_1000uL_filter_wide",
    "hamilton_96_tiprack_10uL",
    "hamilton_96_tiprack_10uL_filter",
    "hamilton_96_tiprack_300uL",
    "hamilton_96_tiprack_300uL_filter",
    "hamilton_96_tiprack_300uL_filter_slim",
    "hamilton_96_tiprack_50uL",
    "hamilton_96_tiprack_50uL_NTR",
    "hamilton_96_tiprack_50uL_filter",
    "opentrons_96_filtertiprack_1000ul",
    "opentrons_96_filtertiprack_10ul",
    "opentrons_96_filtertiprack_200ul",
    "opentrons_96_filtertiprack_20ul",
    "opentrons_96_tiprack_1000ul",
    "opentrons_96_tiprack_10ul",
    "opentrons_96_tiprack_20ul",
    "opentrons_96_tiprack_300ul",
    "thermo_AB_384_wellplate_40uL_Vb_MicroAmp",
    "thermo_AB_96_wellplate_300ul_Vb_MicroAmp",
    "thermo_nunc_1_troughplate_90000uL_Fb_omnitray",
    "tipone_96_tiprack_200ul",
]

# --- Plates ---

from pylabrobot.resources.agenbio.plates import AGenBio_1_troughplate_100000uL_Fl, AGenBio_1_troughplate_190000uL_Fl, AGenBio_1_wellplate_Fl, AGenBio_4_troughplate_75000_Vb, AGenBio_4_troughplate_75000uL_Vb, AGenBio_96_wellplate_Ub_2200ul
from pylabrobot.resources.agilent.plates import agilent_96_wellplate_150uL_Ub, agilent_96_wellplate_150uL_Vb
from pylabrobot.resources.azenta.plates import Azenta4titudeFrameStar_96_wellplate_200ul_Vb
from pylabrobot.resources.bioer.plates import BioER_96_wellplate_Vb_2200uL
from pylabrobot.resources.biorad.plates import BioRad_384_wellplate_50uL_Vb
from pylabrobot.resources.celltreat.plates import CellTreat_12_troughplate_15000ul_Vb, CellTreat_24_wellplate_3300ul_Fb, CellTreat_6_wellplate_16300ul_Fb, CellTreat_96_wellplate_350ul_Fb, CellTreat_96_wellplate_350ul_Ub
from pylabrobot.resources.cellvis.plates import CellVis_24_wellplate_3600uL_Fb, CellVis_96_wellplate_350uL_Fb
from pylabrobot.resources.corning.axygen.plates import Cor_Axy_24_wellplate_10mL_Vb
from pylabrobot.resources.corning.costar.plates import Cor_Cos_12_wellplate_6900ul_Fb, Cor_Cos_24_wellplate_3470ul_Fb, Cor_Cos_48_wellplate_1620ul_Fb, Cor_Cos_6_wellplate_16800ul_Fb
from pylabrobot.resources.corning.falcon.plates import Cor_Falcon_96_wellplate_250ul_Rb, Cor_Falcon_96_wellplate_275ul_Fb, Cor_Falcon_96_wellplate_340ul_Fb_Black
from pylabrobot.resources.corning.plates import Cor_96_wellplate_2mL_Vb, Cor_96_wellplate_360ul_Fb
from pylabrobot.resources.eppendorf.plates import Eppendorf_96_wellplate_250ul_Vb
from pylabrobot.resources.greiner.plates import Greiner_384_wellplate_28ul_Fb
from pylabrobot.resources.perkin_elmer.plates import PerkinElmer_96_wellplate_400ul_Fb
from pylabrobot.resources.porvair.plates import Porvair_24_wellplate_Vb, Porvair_6_reservoir_47ml_Vb
from pylabrobot.resources.revvity.plates import Revvity_384_wellplate_28ul_Ub
from pylabrobot.resources.thermo_fisher.plates import Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate, Thermo_Nunc_96_well_plate_1300uL_Rb, Thermo_TS_96_wellplate_1200ul_Rb, thermo_AB_384_wellplate_40uL_Vb_MicroAmp, thermo_AB_96_wellplate_300ul_Vb_MicroAmp, thermo_nunc_1_troughplate_90000uL_Fb_omnitray
from cheshire_drivers.plr.opentrons_troughs import agilent_1_reservoir_290ml, axygen_1_reservoir_90ml, nest_1_reservoir_195ml
from pylabrobot.resources.vwr.plates import VWR_1_troughplate_195000uL_Ub, VWR_96_wellplate_2mL_Vb

AGenBio_1_troughplate_100000uL_Fl = plr_plate_factory(AGenBio_1_troughplate_100000uL_Fl)
AGenBio_1_troughplate_190000uL_Fl = plr_plate_factory(AGenBio_1_troughplate_190000uL_Fl)
AGenBio_1_wellplate_Fl = plr_plate_factory(AGenBio_1_wellplate_Fl)
AGenBio_4_troughplate_75000_Vb = plr_plate_factory(AGenBio_4_troughplate_75000_Vb)
AGenBio_4_troughplate_75000uL_Vb = plr_plate_factory(AGenBio_4_troughplate_75000uL_Vb)
AGenBio_96_wellplate_Ub_2200ul = plr_plate_factory(AGenBio_96_wellplate_Ub_2200ul)
Azenta4titudeFrameStar_96_wellplate_200ul_Vb = plr_plate_factory(Azenta4titudeFrameStar_96_wellplate_200ul_Vb)
BioER_96_wellplate_Vb_2200uL = plr_plate_factory(BioER_96_wellplate_Vb_2200uL)
BioRad_384_wellplate_50uL_Vb = plr_plate_factory(BioRad_384_wellplate_50uL_Vb)
CellTreat_12_troughplate_15000ul_Vb = plr_plate_factory(CellTreat_12_troughplate_15000ul_Vb)
CellTreat_24_wellplate_3300ul_Fb = plr_plate_factory(CellTreat_24_wellplate_3300ul_Fb)
CellTreat_6_wellplate_16300ul_Fb = plr_plate_factory(CellTreat_6_wellplate_16300ul_Fb)
CellTreat_96_wellplate_350ul_Fb = plr_plate_factory(CellTreat_96_wellplate_350ul_Fb)
CellTreat_96_wellplate_350ul_Ub = plr_plate_factory(CellTreat_96_wellplate_350ul_Ub)
CellVis_24_wellplate_3600uL_Fb = plr_plate_factory(CellVis_24_wellplate_3600uL_Fb)
CellVis_96_wellplate_350uL_Fb = plr_plate_factory(CellVis_96_wellplate_350uL_Fb)
Cor_96_wellplate_2mL_Vb = plr_plate_factory(Cor_96_wellplate_2mL_Vb)
Cor_96_wellplate_360ul_Fb = plr_plate_factory(Cor_96_wellplate_360ul_Fb)
Cor_Axy_24_wellplate_10mL_Vb = plr_plate_factory(Cor_Axy_24_wellplate_10mL_Vb)
Cor_Cos_12_wellplate_6900ul_Fb = plr_plate_factory(Cor_Cos_12_wellplate_6900ul_Fb)
Cor_Cos_24_wellplate_3470ul_Fb = plr_plate_factory(Cor_Cos_24_wellplate_3470ul_Fb)
Cor_Cos_48_wellplate_1620ul_Fb = plr_plate_factory(Cor_Cos_48_wellplate_1620ul_Fb)
Cor_Cos_6_wellplate_16800ul_Fb = plr_plate_factory(Cor_Cos_6_wellplate_16800ul_Fb)
Cor_Falcon_96_wellplate_250ul_Rb = plr_plate_factory(Cor_Falcon_96_wellplate_250ul_Rb)
Cor_Falcon_96_wellplate_275ul_Fb = plr_plate_factory(Cor_Falcon_96_wellplate_275ul_Fb)
Cor_Falcon_96_wellplate_340ul_Fb_Black = plr_plate_factory(Cor_Falcon_96_wellplate_340ul_Fb_Black)
Eppendorf_96_wellplate_250ul_Vb = plr_plate_factory(Eppendorf_96_wellplate_250ul_Vb)
Greiner_384_wellplate_28ul_Fb = plr_plate_factory(Greiner_384_wellplate_28ul_Fb)
PerkinElmer_96_wellplate_400ul_Fb = plr_plate_factory(PerkinElmer_96_wellplate_400ul_Fb)
Porvair_24_wellplate_Vb = plr_plate_factory(Porvair_24_wellplate_Vb)
Porvair_6_reservoir_47ml_Vb = plr_plate_factory(Porvair_6_reservoir_47ml_Vb)
Revvity_384_wellplate_28ul_Ub = plr_plate_factory(Revvity_384_wellplate_28ul_Ub)
Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate = plr_plate_factory(Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate)
Thermo_Nunc_96_well_plate_1300uL_Rb = plr_plate_factory(Thermo_Nunc_96_well_plate_1300uL_Rb)
Thermo_TS_96_wellplate_1200ul_Rb = plr_plate_factory(Thermo_TS_96_wellplate_1200ul_Rb)
VWR_1_troughplate_195000uL_Ub = plr_plate_factory(VWR_1_troughplate_195000uL_Ub)
axygen_1_reservoir_90ml = plr_trough_factory(axygen_1_reservoir_90ml)
nest_1_reservoir_195ml = plr_trough_factory(nest_1_reservoir_195ml)
agilent_1_reservoir_290ml = plr_trough_factory(agilent_1_reservoir_290ml)
VWR_96_wellplate_2mL_Vb = plr_plate_factory(VWR_96_wellplate_2mL_Vb)
agilent_96_wellplate_150uL_Ub = plr_plate_factory(agilent_96_wellplate_150uL_Ub)
agilent_96_wellplate_150uL_Vb = plr_plate_factory(agilent_96_wellplate_150uL_Vb)
thermo_AB_384_wellplate_40uL_Vb_MicroAmp = plr_plate_factory(thermo_AB_384_wellplate_40uL_Vb_MicroAmp)
thermo_AB_96_wellplate_300ul_Vb_MicroAmp = plr_plate_factory(thermo_AB_96_wellplate_300ul_Vb_MicroAmp)
thermo_nunc_1_troughplate_90000uL_Fb_omnitray = plr_plate_factory(thermo_nunc_1_troughplate_90000uL_Fb_omnitray)

# --- Tip Racks ---

from pylabrobot.resources.hamilton.tip_racks import HT, HTF, LT, LTF, ST, STF, STF_Slim, TIP_50ul, TIP_50ul_w_filter, hamilton_24_tiprack_4000uL_filter, hamilton_24_tiprack_5000uL, hamilton_96_tiprack_1000uL, hamilton_96_tiprack_1000uL_filter, hamilton_96_tiprack_1000uL_filter_ultrawide, hamilton_96_tiprack_1000uL_filter_wide, hamilton_96_tiprack_10uL, hamilton_96_tiprack_10uL_filter, hamilton_96_tiprack_300uL, hamilton_96_tiprack_300uL_filter, hamilton_96_tiprack_300uL_filter_slim, hamilton_96_tiprack_50uL, hamilton_96_tiprack_50uL_NTR, hamilton_96_tiprack_50uL_filter
from pylabrobot.resources.opentrons.flex_plates import corning_96_wellplate_360ul_flat
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_filtertiprack_50ul, flex_96_tiprack_1000ul, flex_96_tiprack_200ul, flex_96_tiprack_50ul
from pylabrobot.resources.opentrons.tip_racks import eppendorf_96_tiprack_1000ul_eptips, eppendorf_96_tiprack_10ul_eptips, geb_96_tiprack_1000ul, geb_96_tiprack_10ul, opentrons_96_filtertiprack_1000ul, opentrons_96_filtertiprack_10ul, opentrons_96_filtertiprack_200ul, opentrons_96_filtertiprack_20ul, opentrons_96_tiprack_1000ul, opentrons_96_tiprack_10ul, opentrons_96_tiprack_20ul, opentrons_96_tiprack_300ul, tipone_96_tiprack_200ul

HT = plr_tip_rack_factory(HT)
HTF = plr_tip_rack_factory(HTF)
LT = plr_tip_rack_factory(LT)
LTF = plr_tip_rack_factory(LTF)
ST = plr_tip_rack_factory(ST)
STF = plr_tip_rack_factory(STF)
STF_Slim = plr_tip_rack_factory(STF_Slim)
TIP_50ul = plr_tip_rack_factory(TIP_50ul)
TIP_50ul_w_filter = plr_tip_rack_factory(TIP_50ul_w_filter)
eppendorf_96_tiprack_1000ul_eptips = plr_tip_rack_factory(eppendorf_96_tiprack_1000ul_eptips)
eppendorf_96_tiprack_10ul_eptips = plr_tip_rack_factory(eppendorf_96_tiprack_10ul_eptips)
geb_96_tiprack_1000ul = plr_tip_rack_factory(geb_96_tiprack_1000ul)
geb_96_tiprack_10ul = plr_tip_rack_factory(geb_96_tiprack_10ul)
hamilton_24_tiprack_4000uL_filter = plr_tip_rack_factory(hamilton_24_tiprack_4000uL_filter)
hamilton_24_tiprack_5000uL = plr_tip_rack_factory(hamilton_24_tiprack_5000uL)
hamilton_96_tiprack_1000uL = plr_tip_rack_factory(hamilton_96_tiprack_1000uL)
hamilton_96_tiprack_1000uL_filter = plr_tip_rack_factory(hamilton_96_tiprack_1000uL_filter)
hamilton_96_tiprack_1000uL_filter_ultrawide = plr_tip_rack_factory(hamilton_96_tiprack_1000uL_filter_ultrawide)
hamilton_96_tiprack_1000uL_filter_wide = plr_tip_rack_factory(hamilton_96_tiprack_1000uL_filter_wide)
hamilton_96_tiprack_10uL = plr_tip_rack_factory(hamilton_96_tiprack_10uL)
hamilton_96_tiprack_10uL_filter = plr_tip_rack_factory(hamilton_96_tiprack_10uL_filter)
hamilton_96_tiprack_300uL = plr_tip_rack_factory(hamilton_96_tiprack_300uL)
hamilton_96_tiprack_300uL_filter = plr_tip_rack_factory(hamilton_96_tiprack_300uL_filter)
hamilton_96_tiprack_300uL_filter_slim = plr_tip_rack_factory(hamilton_96_tiprack_300uL_filter_slim)
hamilton_96_tiprack_50uL = plr_tip_rack_factory(hamilton_96_tiprack_50uL)
hamilton_96_tiprack_50uL_NTR = plr_tip_rack_factory(hamilton_96_tiprack_50uL_NTR)
hamilton_96_tiprack_50uL_filter = plr_tip_rack_factory(hamilton_96_tiprack_50uL_filter)
opentrons_96_filtertiprack_1000ul = plr_tip_rack_factory(opentrons_96_filtertiprack_1000ul)
opentrons_96_filtertiprack_10ul = plr_tip_rack_factory(opentrons_96_filtertiprack_10ul)
opentrons_96_filtertiprack_200ul = plr_tip_rack_factory(opentrons_96_filtertiprack_200ul)
corning_96_wellplate_360ul_flat = plr_plate_factory(corning_96_wellplate_360ul_flat)
flex_96_filtertiprack_50ul = plr_tip_rack_factory(flex_96_filtertiprack_50ul)
flex_96_tiprack_1000ul = plr_tip_rack_factory(flex_96_tiprack_1000ul)
flex_96_tiprack_200ul = plr_tip_rack_factory(flex_96_tiprack_200ul)
flex_96_tiprack_50ul = plr_tip_rack_factory(flex_96_tiprack_50ul)
opentrons_96_filtertiprack_20ul = plr_tip_rack_factory(opentrons_96_filtertiprack_20ul)
opentrons_96_tiprack_1000ul = plr_tip_rack_factory(opentrons_96_tiprack_1000ul)
opentrons_96_tiprack_10ul = plr_tip_rack_factory(opentrons_96_tiprack_10ul)
opentrons_96_tiprack_20ul = plr_tip_rack_factory(opentrons_96_tiprack_20ul)
opentrons_96_tiprack_300ul = plr_tip_rack_factory(opentrons_96_tiprack_300ul)
tipone_96_tiprack_200ul = plr_tip_rack_factory(tipone_96_tiprack_200ul)

# --- Troughs ---

from pylabrobot.resources.hamilton.troughs import hamilton_1_trough_200ml_Vb, hamilton_1_trough_60ml_Vb
from pylabrobot.resources.thermo_fisher.troughs import ThermoFisherMatrixTrough8094
from pylabrobot.resources.vwr.troughs import VWRReagentReservoirs25mL

ThermoFisherMatrixTrough8094 = plr_trough_factory(ThermoFisherMatrixTrough8094)
VWRReagentReservoirs25mL = plr_trough_factory(VWRReagentReservoirs25mL)
hamilton_1_trough_200ml_Vb = plr_trough_factory(hamilton_1_trough_200ml_Vb)
hamilton_1_trough_60ml_Vb = plr_trough_factory(hamilton_1_trough_60ml_Vb)

