from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("shared-peer") / "bootstrap-local-peer.ps1"


class BootstrapLocalPeerTests(unittest.TestCase):
    def test_peer_role_is_explicitly_read_only(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("REVOKE CREATE ON DATABASE", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON SCHEMA quant", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA quant", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA quant", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA quant", source)
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL PROCEDURES IN SCHEMA quant", source)
        self.assertIn("REVOKE ALL ON FUNCTIONS FROM", source)
        self.assertIn("REVOKE ALL ON TABLES FROM", source)
        self.assertIn("GRANT SELECT ON ALL TABLES IN SCHEMA quant", source)
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT", source)
        self.assertIn("CREATE ROLE $PeerRole LOGIN NOINHERIT", source)
        self.assertIn("ALTER ROLE $PeerRole LOGIN NOINHERIT", source)
        self.assertIn("statement_timeout = '15min'", source)
        self.assertIn("idle_in_transaction_session_timeout = '5min'", source)
        self.assertIn("PeerN8nDatabase", source)
        self.assertIn("[switch]$DryRun", source)
        self.assertIn("QuantOwnedObjectCount", source)
        self.assertIn("SecurityDefinerExecutableCount", source)
        self.assertIn("WritableQuantRelationCount", source)
        self.assertIn("WritableQuantSequenceCount", source)
        self.assertIn("has_sequence_privilege", source)
        self.assertIn("prosecdef", source)
        self.assertIn("ReadOnlyReady", source)
        self.assertIn("RoleInherit", source)
        self.assertIn("RoleReplication", source)
        self.assertIn("RoleBypassRls", source)
        self.assertIn("RoleMembershipCount", source)
        self.assertIn("UnexpectedOwnedDatabaseCount", source)
        self.assertIn("OwnedTablespaceCount", source)
        self.assertIn("pg_auth_members", source)
        self.assertIn("REVOKE %I FROM %I", source)
        self.assertIn("PGDATABASE", source)
        self.assertIn("PGUSER", source)
        self.assertIn("REASSIGN OWNED BY $PeerRole TO $($runtime.PGADMINUSER)", source)
        self.assertIn("unexpected shared objects", source)
        self.assertIn("pg_tablespace", source)
        self.assertIn("datname NOT IN ('$($runtime.PGDATABASE)', '$PeerN8nDatabase')", source)
        self.assertIn("ALTER DATABASE $PeerN8nDatabase OWNER TO $PeerRole", source)
        self.assertIn("pg_get_userbyid(datdba)", source)
        self.assertIn("ALTER DATABASE $($runtime.PGDATABASE) OWNER TO $($runtime.PGADMINUSER)", source)
        self.assertIn("pg_get_userbyid(nspowner)", source)
        self.assertIn("ALTER SCHEMA quant OWNER TO $($runtime.PGADMINUSER)", source)
        self.assertIn("rolinherit FROM pg_roles", source)
        self.assertIn("datdba", source)
        self.assertIn("nspowner", source)
        self.assertIn("c.relowner", source)
        self.assertIn("has_schema_privilege('$PeerRole','quant','CREATE')", source)
        self.assertIn("has_table_privilege('$PeerRole','quant.canonical_bars_daily','INSERT')", source)
        self.assertIn("still inherits or has role memberships, owns an owner object, has executable SECURITY DEFINER routines, or has quant schema/relation/sequence write privileges", source)


if __name__ == "__main__":
    unittest.main()
