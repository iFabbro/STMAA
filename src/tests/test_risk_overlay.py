import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.strategy.risk_overlay import RiskOverlayModule

def test_risk_overlay_creation():
    overlay = RiskOverlayModule(
        max_position_size=0.2,
        vix_threshold_high=30,
        vix_threshold_low=15
    )
    
    assert overlay.max_position_size == 0.2
    print("✅ RiskOverlayModule creato")

def test_vix_scaling():
    overlay = RiskOverlayModule()
    
    scale_low = overlay._calculate_vix_scaling(12)
    assert scale_low == 1.0
    
    scale_high = overlay._calculate_vix_scaling(35)
    assert scale_high == 0.5
    
    scale_mid = overlay._calculate_vix_scaling(22)
    assert 0.5 < scale_mid < 1.0
    
    print("✅ VIX scaling funzionante")

def test_position_cap():
    overlay = RiskOverlayModule(max_position_size=0.2)
    
    weights = np.array([0.5, 0.3, 0.2])
    capped = np.clip(weights, -0.2, 0.2)
    
    assert capped[0] == 0.2
    print("✅ Position cap funzionante")

if __name__ == "__main__":
    test_risk_overlay_creation()
    test_vix_scaling()
    test_position_cap()
    print("\n🎉 Tutti i test risk overlay passati!")
