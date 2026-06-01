"""Data processor per ingesta e normalizza dati di mercato"""
import pandas as pd
import numpy as np
import yfinance as yf
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timedelta

class DataProcessor:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.raw_dir = data_dir / "raw"
        self.processed_dir = data_dir / "processed"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
    
    def download_data(self, tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """Scarica dati OHLCV da Yahoo Finance"""
        print(f"⬇️  Scaricamento dati per {len(tickers)} tickers...")
        data = yf.download(tickers, start=start_date, end=end_date, auto_adjust=True)
        
        # Salva raw data
        raw_file = self.raw_dir / f"raw_{start_date}_{end_date}.csv"
        data.to_csv(raw_file)
        print(f"✅ Dati salvati in {raw_file}")
        
        return data
    
    def clean_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Pulisce dati: gestisce NaN, forward fill per alta frequenza"""
        cleaned = data.copy()
        
        # Gestione NaN: forward fill per colonna close, volume=0
        for col in ['Open', 'High', 'Low', 'Close']:
            cleaned[col] = cleaned[col].ffill().bfill()
        cleaned['Volume'] = cleaned['Volume'].fillna(0)
        
        # Rimuovi righe completamente vuote
        cleaned = cleaned.dropna(how='all')
        
        return cleaned
    
    def add_technical_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Calcola indicatori tecnici: RSI, MACD, Bollinger Bands"""
        df = data.copy()
        
        # RSI (14 giorni)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # MACD
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        
        # Bollinger Bands
        df['BB_middle'] = df['Close'].rolling(window=20).mean()
        df['BB_std'] = df['Close'].rolling(window=20).std()
        df['BB_upper'] = df['BB_middle'] + (df['BB_std'] * 2)
        df['BB_lower'] = df['BB_middle'] - (df['BB_std'] * 2)
        
        # Volatilità (ATR semplificato)
        df['TR'] = np.maximum(
            df['High'] - df['Low'],
            np.abs(df['High'] - df['Close'].shift(1))
        )
        df['ATR'] = df['TR'].rolling(window=14).mean()
        
        return df
    
    def process(self, tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """Pipeline completa: download → cleaning → feature engineering"""
        data = self.download_data(tickers, start_date, end_date)
        cleaned = self.clean_data(data)
        processed = self.add_technical_indicators(cleaned)
        
        # Salva processed data
        processed_file = self.processed_dir / f"processed_{start_date}_{end_date}.csv"
        processed.to_csv(processed_file)
        print(f"✅ Dati processati salvati in {processed_file}")
        
        return processed
