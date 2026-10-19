"""Builds the tool registry (the only place tools are instantiated)."""

from __future__ import annotations

from typing import Any

from app.db.base import Database
from app.tools.actions import CreateSupportCase, RequestTerminalReplacement
from app.tools.base import Tool, ToolRegistry
from app.tools.customer import (
    GetCustomerProfile,
    GetCustomerTerminals,
    GetOpenSupportCases,
    GetRecentTransactions,
    GetSettlementSchedule,
    GetTerminalStatus,
    GetTransactionDetails,
)


def build_registry(db: Database) -> ToolRegistry:
    tools: list[Tool[Any]] = [
        GetCustomerProfile(db),
        GetCustomerTerminals(db),
        GetTerminalStatus(db),
        GetRecentTransactions(db),
        GetTransactionDetails(db),
        GetSettlementSchedule(db),
        GetOpenSupportCases(db),
        CreateSupportCase(db),
        RequestTerminalReplacement(db),
    ]
    return ToolRegistry(tools)
