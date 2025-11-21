"""
Configuration Manager for GitHub Actions Performance Analyzer

Manages secure storage and retrieval of GitHub tokens with encryption support.
Provides fallback to environment variables for backward compatibility.
"""

import json
import os
from typing import Optional
from datetime import datetime
from pathlib import Path
from cryptography.fernet import Fernet
import hashlib


class ConfigManager:
    """Manages application configuration with secure token storage."""
    
    def __init__(self, config_path: str = "data/config.json"):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Path to the configuration file (default: data/config.json)
        """
        self.config_path = Path(config_path)
        self._encryption_key = self._get_or_create_encryption_key()
        self._cipher = Fernet(self._encryption_key)
        
    def _get_or_create_encryption_key(self) -> bytes:
        """
        Get or create encryption key for token storage.
        
        Uses a combination of hostname and a seed file to generate a consistent
        encryption key per installation.
        
        Returns:
            Fernet-compatible encryption key
        """
        key_file = self.config_path.parent / ".encryption_key"
        
        if key_file.exists():
            with open(key_file, 'rb') as f:
                return f.read()
        
        # Generate new key
        key = Fernet.generate_key()
        
        # Ensure directory exists
        key_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Store key with restricted permissions
        with open(key_file, 'wb') as f:
            f.write(key)
        
        # Set file permissions to 600 (owner read/write only)
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass  # Windows doesn't support chmod
            
        return key
    
    def get_github_token(self) -> Optional[str]:
        """
        Retrieve GitHub token with priority order:
        1. Environment variable (GITHUB_TOKEN)
        2. Stored configuration file
        
        Returns:
            GitHub token string or None if not configured
        """
        # Check environment variable first (highest priority)
        env_token = os.environ.get('GITHUB_TOKEN')
        if env_token:
            return env_token
        
        # Check stored configuration
        config = self._load_config()
        if config and config.get('github_token_encrypted'):
            try:
                encrypted_token = config['github_token_encrypted']
                return self.decrypt_token(encrypted_token)
            except Exception as e:
                print(f"Warning: Failed to decrypt stored token: {e}")
                return None
        
        return None
    
    def set_github_token(self, token: str) -> bool:
        """
        Store GitHub token securely in persistent storage.
        
        Args:
            token: GitHub personal access token
            
        Returns:
            True on success, False on failure
        """
        if not token or not isinstance(token, str):
            return False
        
        try:
            # Encrypt the token
            encrypted_token = self.encrypt_token(token)
            
            # Load existing config or create new
            config = self._load_config() or {}
            
            # Update config
            config['github_token_encrypted'] = encrypted_token
            config['encryption_key_hash'] = self._get_key_hash()
            config['updated_at'] = datetime.utcnow().isoformat() + 'Z'
            
            # Save config
            self._save_config(config)
            
            return True
        except Exception as e:
            print(f"Error storing GitHub token: {e}")
            return False
    
    def remove_github_token(self) -> bool:
        """
        Remove stored GitHub token from configuration.
        
        Returns:
            True on success, False on failure
        """
        try:
            config = self._load_config()
            if not config:
                return True  # Nothing to remove
            
            # Remove token-related fields
            config.pop('github_token_encrypted', None)
            config.pop('encryption_key_hash', None)
            config['updated_at'] = datetime.utcnow().isoformat() + 'Z'
            
            # Save updated config
            self._save_config(config)
            
            return True
        except Exception as e:
            print(f"Error removing GitHub token: {e}")
            return False
    
    def is_token_configured(self) -> bool:
        """
        Check if a valid token is available from any source.
        
        Returns:
            True if token is configured, False otherwise
        """
        return self.get_github_token() is not None
    
    def get_token_source(self) -> str:
        """
        Determine the source of the configured token.
        
        Returns:
            'environment', 'stored', or 'none'
        """
        if os.environ.get('GITHUB_TOKEN'):
            return 'environment'
        
        config = self._load_config()
        if config and config.get('github_token_encrypted'):
            return 'stored'
        
        return 'none'
    
    def encrypt_token(self, token: str) -> str:
        """
        Encrypt token using Fernet symmetric encryption.
        
        Args:
            token: Plaintext token
            
        Returns:
            Encrypted token as string
        """
        encrypted_bytes = self._cipher.encrypt(token.encode('utf-8'))
        return encrypted_bytes.decode('utf-8')
    
    def decrypt_token(self, encrypted_token: str) -> str:
        """
        Decrypt stored token.
        
        Args:
            encrypted_token: Encrypted token string
            
        Returns:
            Decrypted plaintext token
        """
        decrypted_bytes = self._cipher.decrypt(encrypted_token.encode('utf-8'))
        return decrypted_bytes.decode('utf-8')
    
    def _load_config(self) -> Optional[dict]:
        """
        Load configuration from JSON file.
        
        Returns:
            Configuration dictionary or None if file doesn't exist
        """
        if not self.config_path.exists():
            return None
        
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load config: {e}")
            return None
    
    def _save_config(self, config: dict) -> None:
        """
        Save configuration to JSON file.
        
        Args:
            config: Configuration dictionary to save
        """
        # Ensure directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write config file
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Set file permissions to 600 (owner read/write only)
        try:
            os.chmod(self.config_path, 0o600)
        except Exception:
            pass  # Windows doesn't support chmod
    
    def _get_key_hash(self) -> str:
        """
        Get SHA256 hash of encryption key for verification.
        
        Returns:
            Hex string of key hash
        """
        return hashlib.sha256(self._encryption_key).hexdigest()
