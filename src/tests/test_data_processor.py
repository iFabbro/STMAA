import pandas as pd
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.data.data_processor import DataProcessor

def test_data_processor_creation():
    processor = DataProcessor(Path("data"))
    assert processor.data_dir.exists()
    print("✅ DataProcessor creato con successo")

def test_technical_indicators():
    processor = DataProcessor(Path("data"))
    # Crea dati mock
    mock_data = pd.DataFrame({
        'Close': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115],
        'High': [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116],
        'Low': [99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114],
        'Open': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115],
        'Volume': [1000]*16
    })
    
    processed = processor.add_technical_indicators(mock_data)
    
    assert 'RSI' in processed.columns
    assert 'MACD' in processed.columns
    assert 'BB_middle' in processed.columns
    assert not processed['RSI'].isna().all()
    print("✅ Indicatori tecnici calcolati correttamente")

if __name__ == "__main__":
    test_data_processor_creation()
    test_technical_indicators()
    print("\n🎉 Tutti i test data processor passati!")
