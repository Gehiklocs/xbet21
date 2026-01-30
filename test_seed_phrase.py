from mnemonic import Mnemonic
from bip32utils import BIP32Key
import requests
import json


class HDWalletManager:
    def __init__(self, seed_phrase=None):
        if seed_phrase:
            self.load_from_seed(seed_phrase)
        else:
            self.create_new()

    def create_new(self):
        """Create new wallet"""
        mnemo = Mnemonic("english")
        self.seed_phrase = mnemo.generate(strength=256)
        self._init_wallet()
        print(f"New wallet created. Seed phrase:\n{self.seed_phrase}\n")

    def load_from_seed(self, seed_phrase):
        """Load existing wallet from seed phrase"""
        self.seed_phrase = seed_phrase
        self._init_wallet()
        print(f"Wallet loaded from seed phrase.\n")

    def _init_wallet(self):
        """Initialize wallet from seed"""
        mnemo = Mnemonic("english")
        seed = mnemo.to_seed(self.seed_phrase)
        self.master_key = BIP32Key.fromEntropy(seed)

        # Generate first 5 addresses
        self.addresses = []
        for i in range(5):
            key = (self.master_key
                   .ChildKey(44 + 0x80000000)
                   .ChildKey(0 + 0x80000000)
                   .ChildKey(0 + 0x80000000)
                   .ChildKey(0)
                   .ChildKey(i))
            self.addresses.append(key.Address())

    def get_balances(self):
        """Check balances for all addresses"""
        print("=" * 60)
        print("CHECKING WALLET BALANCES")
        print("=" * 60)

        total_btc = 0
        total_usd = 0

        # Get Bitcoin price
        try:
            btc_price = self._get_btc_price()
        except:
            btc_price = 0

        for i, addr in enumerate(self.addresses):
            balance_btc = self._check_address_balance(addr)
            balance_usd = balance_btc * btc_price

            total_btc += balance_btc
            total_usd += balance_usd

            print(f"[{i}] {addr}")
            if balance_btc > 0:
                print(f"     Balance: {balance_btc:.8f} BTC")
                print(f"             ≈ ${balance_usd:.2f} USD")
            else:
                print(f"     Balance: 0 BTC")
            print()

        print("-" * 60)
        print(f"💰 TOTAL: {total_btc:.8f} BTC")
        if btc_price > 0:
            print(f"         ≈ ${total_usd:.2f} USD")
        print("=" * 60)

        return total_btc

    def _check_address_balance(self, address):
        """Check single address balance"""
        try:
            url = f"https://blockchain.info/balance?active={address}"
            response = requests.get(url, timeout=10)
            data = response.json()
            satoshis = data[address]['final_balance']
            return satoshis / 100_000_000
        except:
            return 0

    def _get_btc_price(self):
        """Get current Bitcoin price"""
        try:
            response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
                                    timeout=5)
            return response.json()['bitcoin']['usd']
        except:
            return 0

    def show_wallet_info(self):
        """Display wallet information"""
        print("=" * 60)
        print("WALLET INFORMATION")
        print("=" * 60)
        print(f"Seed phrase: {self.seed_phrase}")
        print("\nAddresses:")
        for i, addr in enumerate(self.addresses):
            print(f"  [{i}] {addr}")
        print("=" * 60)


# Usage:
# Option A: Create new wallet
# wallet = HDWalletManager()
# wallet.show_wallet_info()
# wallet.get_balances()

# Option B: Load existing wallet
existing_seed = "limb elite relief day defy provide bracket dial whale prosper primary father"
wallet = HDWalletManager(existing_seed)
wallet.get_balances()