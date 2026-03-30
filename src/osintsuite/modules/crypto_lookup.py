"""Cryptocurrency lookup module — wallet addresses, balances, and transaction history."""

from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class CryptoLookupModule(BaseModule):
    name = "crypto_lookup"
    description = "Cryptocurrency address and transaction lookup"

    def __init__(self, *args, etherscan_api_key: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.etherscan_api_key = etherscan_api_key or "YourApiKeyToken"

    def applicable_target_types(self) -> list[str]:
        return ["person", "email"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        label = target.label
        email = target.email or (label if target.target_type == "email" else None)
        metadata = target.metadata_ or {}

        btc_address = metadata.get("btc_address")
        eth_address = metadata.get("eth_address")

        # Check BTC address
        if btc_address:
            results.extend(await self._lookup_btc(btc_address))

        # Check ETH address
        if eth_address:
            results.extend(await self._lookup_eth(eth_address))

        # If no crypto addresses in metadata, search via DDG dork
        if not btc_address and not eth_address:
            results.extend(await self._dork_crypto(label, email))

        # Summary
        wallet_results = [r for r in results if r.finding_type == "crypto_wallet"]
        mention_results = [r for r in results if r.finding_type == "crypto_mention"]
        results.append(
            ModuleResult(
                module_name=self.name,
                source="crypto_lookup",
                finding_type="crypto_summary",
                title=f"Crypto lookup summary for {label}",
                content=(
                    f"Found {len(wallet_results)} wallet(s) and "
                    f"{len(mention_results)} crypto mention(s)."
                ),
                data={
                    "label": label,
                    "wallets_found": len(wallet_results),
                    "mentions_found": len(mention_results),
                    "btc_checked": bool(btc_address),
                    "eth_checked": bool(eth_address),
                },
                confidence=60,
            )
        )

        return results

    async def _lookup_btc(self, address: str) -> list[ModuleResult]:
        """Look up a Bitcoin address via blockchain.info API."""
        results: list[ModuleResult] = []
        url = f"https://blockchain.info/rawaddr/{address}?limit=10"

        response = await self.fetch(url)
        if not response:
            return results

        try:
            data = response.json()

            # Convert satoshi to BTC
            balance_btc = data.get("final_balance", 0) / 1e8
            total_received_btc = data.get("total_received", 0) / 1e8
            total_sent_btc = data.get("total_sent", 0) / 1e8
            tx_count = data.get("n_tx", 0)

            # Extract first/last seen from transactions
            txs = data.get("txs", [])
            first_seen = ""
            last_seen = ""
            if txs:
                times = [tx.get("time", 0) for tx in txs if tx.get("time")]
                if times:
                    import datetime
                    first_seen = datetime.datetime.fromtimestamp(
                        min(times), tz=datetime.timezone.utc
                    ).isoformat()
                    last_seen = datetime.datetime.fromtimestamp(
                        max(times), tz=datetime.timezone.utc
                    ).isoformat()

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="blockchain.info",
                    finding_type="crypto_wallet",
                    title=f"BTC wallet: {address[:16]}...{address[-8:]}",
                    content=(
                        f"Bitcoin address {address}\n"
                        f"Balance: {balance_btc:.8f} BTC\n"
                        f"Total received: {total_received_btc:.8f} BTC\n"
                        f"Total sent: {total_sent_btc:.8f} BTC\n"
                        f"Transactions: {tx_count}"
                    ),
                    data={
                        "address": address,
                        "type": "BTC",
                        "balance": balance_btc,
                        "total_received": total_received_btc,
                        "total_sent": total_sent_btc,
                        "tx_count": tx_count,
                        "first_seen": first_seen,
                        "last_seen": last_seen,
                    },
                    confidence=90,
                )
            )

            # Add recent transaction details
            for tx in txs[:5]:
                tx_hash = tx.get("hash", "unknown")
                tx_time = tx.get("time", 0)
                tx_timestamp = ""
                if tx_time:
                    import datetime
                    tx_timestamp = datetime.datetime.fromtimestamp(
                        tx_time, tz=datetime.timezone.utc
                    ).isoformat()

                # Calculate net value for this address
                inputs_val = sum(
                    inp.get("prev_out", {}).get("value", 0)
                    for inp in tx.get("inputs", [])
                    if inp.get("prev_out", {}).get("addr") == address
                )
                outputs_val = sum(
                    out.get("value", 0)
                    for out in tx.get("out", [])
                    if out.get("addr") == address
                )
                net_btc = (outputs_val - inputs_val) / 1e8

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="blockchain.info",
                        finding_type="crypto_transaction",
                        title=f"BTC TX: {tx_hash[:16]}...",
                        content=f"Hash: {tx_hash}, Time: {tx_timestamp}, Net: {net_btc:+.8f} BTC",
                        data={
                            "tx_hash": tx_hash,
                            "timestamp": tx_timestamp,
                            "net_value_btc": net_btc,
                            "address": address,
                            "type": "BTC",
                        },
                        confidence=90,
                    )
                )

        except Exception as e:
            self.logger.warning(f"BTC lookup failed for {address}: {e}")

        return results

    async def _lookup_eth(self, address: str) -> list[ModuleResult]:
        """Look up an Ethereum address via Etherscan API."""
        results: list[ModuleResult] = []

        # Get balance
        balance_url = (
            f"https://api.etherscan.io/api"
            f"?module=account&action=balance&address={address}"
            f"&tag=latest&apikey={self.etherscan_api_key}"
        )
        balance_resp = await self.fetch(balance_url)

        balance_eth = 0.0
        if balance_resp:
            try:
                data = balance_resp.json()
                if data.get("status") == "1":
                    balance_wei = int(data.get("result", "0"))
                    balance_eth = balance_wei / 1e18
            except Exception as e:
                self.logger.warning(f"ETH balance parse failed for {address}: {e}")

        # Get transaction count
        txcount_url = (
            f"https://api.etherscan.io/api"
            f"?module=proxy&action=eth_getTransactionCount&address={address}"
            f"&tag=latest&apikey={self.etherscan_api_key}"
        )
        txcount_resp = await self.fetch(txcount_url)

        tx_count = 0
        if txcount_resp:
            try:
                data = txcount_resp.json()
                result = data.get("result", "0x0")
                if result and result.startswith("0x"):
                    tx_count = int(result, 16)
            except Exception as e:
                self.logger.warning(f"ETH tx count parse failed for {address}: {e}")

        # Get recent transactions for first/last seen
        txlist_url = (
            f"https://api.etherscan.io/api"
            f"?module=account&action=txlist&address={address}"
            f"&startblock=0&endblock=99999999&page=1&offset=5&sort=asc"
            f"&apikey={self.etherscan_api_key}"
        )
        txlist_resp = await self.fetch(txlist_url)

        first_seen = ""
        last_seen = ""
        if txlist_resp:
            try:
                data = txlist_resp.json()
                txs = data.get("result", [])
                if isinstance(txs, list) and txs:
                    import datetime
                    timestamps = [int(tx.get("timeStamp", 0)) for tx in txs if tx.get("timeStamp")]
                    if timestamps:
                        first_seen = datetime.datetime.fromtimestamp(
                            min(timestamps), tz=datetime.timezone.utc
                        ).isoformat()
                        last_seen = datetime.datetime.fromtimestamp(
                            max(timestamps), tz=datetime.timezone.utc
                        ).isoformat()
            except Exception as e:
                self.logger.warning(f"ETH txlist parse failed: {e}")

        results.append(
            ModuleResult(
                module_name=self.name,
                source="etherscan",
                finding_type="crypto_wallet",
                title=f"ETH wallet: {address[:16]}...{address[-8:]}",
                content=(
                    f"Ethereum address {address}\n"
                    f"Balance: {balance_eth:.6f} ETH\n"
                    f"Transactions: {tx_count}"
                ),
                data={
                    "address": address,
                    "type": "ETH",
                    "balance": balance_eth,
                    "tx_count": tx_count,
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                },
                confidence=85,
            )
        )

        return results

    async def _dork_crypto(self, label: str, email: str | None) -> list[ModuleResult]:
        """Search DuckDuckGo for crypto wallet references related to the target."""
        results: list[ModuleResult] = []

        search_terms = [f'"{label}"']
        if email and email != label:
            search_terms.append(f'OR "{email}"')
        search_terms.append("bitcoin OR ethereum OR crypto wallet")

        query = urllib.parse.quote_plus(" ".join(search_terms))
        url = f"https://html.duckduckgo.com/html/?q={query}"

        response = await self.fetch(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
            },
        )
        if not response:
            return results

        try:
            link_pattern = re.compile(
                r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                re.DOTALL | re.IGNORECASE,
            )
            snippet_pattern = re.compile(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|span|td)',
                re.DOTALL | re.IGNORECASE,
            )

            links = link_pattern.findall(response.text)
            snippets = snippet_pattern.findall(response.text)

            for i, (href, title) in enumerate(links[:5]):
                clean_title = re.sub(r"<[^>]+>", "", title).strip()
                clean_snippet = ""
                if i < len(snippets):
                    clean_snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

                if clean_title:
                    # Check if snippet contains a crypto address pattern
                    btc_match = re.search(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b", clean_snippet)
                    eth_match = re.search(r"\b0x[a-fA-F0-9]{40}\b", clean_snippet)

                    detected_address = None
                    addr_type = None
                    if btc_match:
                        detected_address = btc_match.group(0)
                        addr_type = "BTC"
                    elif eth_match:
                        detected_address = eth_match.group(0)
                        addr_type = "ETH"

                    result_data = {
                        "url": href,
                        "title": clean_title,
                        "snippet": clean_snippet[:500],
                        "label": label,
                    }
                    if detected_address:
                        result_data["detected_address"] = detected_address
                        result_data["detected_type"] = addr_type

                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="dork_ddg",
                            finding_type="crypto_mention",
                            title=f"Crypto ref: {clean_title[:80]}",
                            content=clean_snippet[:300] if clean_snippet else clean_title,
                            data=result_data,
                            confidence=35 if not detected_address else 55,
                        )
                    )
        except Exception as e:
            self.logger.warning(f"DDG crypto dork parsing failed: {e}")

        return results
