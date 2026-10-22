"""Persistence layer (taskbook R10+): PostgreSQL contracts, migrations,
and the unit-of-work boundary. JSON repositories remain the production
store until R15's full-domain migration; nothing here dual-writes.
"""
