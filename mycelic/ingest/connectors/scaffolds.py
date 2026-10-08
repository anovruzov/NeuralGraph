"""Planned connectors (status ``scaffold``): the manifest exists so owners and administrators can see the roadmap and the
permissions each app would ask for; the registry refuses to create them, the catalog lists them as planned, and no
code path pretends they work.

The permissions listed are the least-privilege read scopes the design calls for. They are taken from each provider's
public documentation as understood when this file was written and must be re-verified against the provider before the
connector is implemented (docs/mycelic/INGESTION.md §16). Each becomes a real connector by subclassing
:class:`~mycelic.ingest.contract.Connector` like ``github`` or ``local_export``, with offline fixtures first.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from ..contract import (AuthStart, Capabilities, ConnectResult, Connector, ConnectorContext, ConnectorManifest, Cursor, Page,
                        PermanentError, RawItem, ScopeSpec, SourceDescriptor, BackfillWindow)

_NOT_YET = "planned connector: not implemented yet"


class ScaffoldConnector(Connector):
    """Every operation refuses with ``code='scaffold'``."""

    async def authorize(self, *, tenant_id: str, holder_id: str, redirect_uri: str, state: str) -> AuthStart:
        raise PermanentError(_NOT_YET, code="scaffold")

    async def connect(self, ctx: ConnectorContext) -> ConnectResult:
        raise PermanentError(_NOT_YET, code="scaffold")

    async def discover_sources(self, ctx: ConnectorContext) -> AsyncIterator[SourceDescriptor]:  # type: ignore[override]
        raise PermanentError(_NOT_YET, code="scaffold")
        yield  # pragma: no cover

    async def initial_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, window: BackfillWindow,  # type: ignore[override]
                               cursor: Cursor | None) -> AsyncIterator[Page]:
        raise PermanentError(_NOT_YET, code="scaffold")
        yield  # pragma: no cover

    async def incremental_sync(self, ctx: ConnectorContext, source: SourceDescriptor, cursor: Cursor | None) -> AsyncIterator[Page]:  # type: ignore[override]
        raise PermanentError(_NOT_YET, code="scaffold")
        yield  # pragma: no cover

    def normalize(self, raw: RawItem, ctx: ConnectorContext) -> list[Any]:
        raise PermanentError(_NOT_YET, code="scaffold")


def _scaffold(connector_type: str, display_name: str, *, auth: tuple[str, ...], scopes: tuple[tuple[str, str], ...], source_types: tuple[str, ...],
              hosts: tuple[str, ...], modes: tuple[str, ...] = ("pull",), acl: str = "full", deletes: str = "reconcile", threads: bool = False,
              ownership: tuple[str, ...] = ("personal", "org"), notes: str = "") -> type[ScaffoldConnector]:
    manifest = ConnectorManifest(
        connector_type=connector_type, display_name=display_name, version="0.0.1", status="scaffold", auth_kinds=auth,
        scopes=tuple(ScopeSpec(s, True, r) for s, r in scopes), modes=frozenset(modes), source_types=source_types,
        capabilities=Capabilities(edits=True, deletes=deletes, threads=threads, attachments=False, acl=acl, exports=False),
        allowed_hosts=hosts, terms_notes=("Planned. " + notes).strip(), ownership=ownership)
    return type(f"{''.join(p.title() for p in connector_type.split('_'))}Scaffold", (ScaffoldConnector,), {"manifest": manifest})


_GRAPH = ("graph.microsoft.com", "login.microsoftonline.com")
_GOOGLE = ("oauth2.googleapis.com", "www.googleapis.com")

SCAFFOLD_CONNECTORS: tuple[type[ScaffoldConnector], ...] = (
    _scaffold("gmail", "Gmail", auth=("oauth2",), scopes=(("https://www.googleapis.com/auth/gmail.readonly", "Read messages and labels you choose to include"),),
              source_types=("mailbox", "label", "thread"), hosts=_GOOGLE + ("gmail.googleapis.com",), modes=("pull", "webhook"), deletes="webhook",
              threads=True, ownership=("personal",), notes="Next after GitHub and Slack. Personal mailboxes only; labels are chosen explicitly."),
    _scaffold("google_drive", "Google Drive", auth=("oauth2",), scopes=(("https://www.googleapis.com/auth/drive.readonly", "Read the files and shared drives you include"),),
              source_types=("folder", "shared_drive"), hosts=_GOOGLE, modes=("pull", "webhook"), deletes="webhook",
              notes="Next after GitHub and Slack. File permissions become record ACLs."),
    _scaffold("google_calendar", "Google Calendar", auth=("oauth2",), scopes=(("https://www.googleapis.com/auth/calendar.readonly", "Read events of the calendars you include"),),
              source_types=("calendar",), hosts=_GOOGLE, ownership=("personal",)),
    _scaffold("google_docs", "Google Docs", auth=("oauth2",), scopes=(("https://www.googleapis.com/auth/documents.readonly", "Read the documents you include"),),
              source_types=("folder", "document"), hosts=_GOOGLE + ("docs.googleapis.com",)),
    _scaffold("microsoft_teams", "Microsoft Teams", auth=("oauth2",),
              scopes=(("ChannelMessage.Read.All", "Read messages of the channels you include (needs tenant admin consent)"),
                      ("Chat.Read", "Read your chats, only if you opt in"), ("offline_access", "Keep syncing without signing in again")),
              source_types=("channel", "chat"), hosts=_GRAPH, modes=("pull", "webhook"), deletes="webhook", threads=True),
    _scaffold("outlook", "Outlook mail", auth=("oauth2",), scopes=(("Mail.Read", "Read the folders you include"), ("offline_access", "Keep syncing without signing in again")),
              source_types=("mailbox", "folder"), hosts=_GRAPH, modes=("pull", "webhook"), deletes="webhook", threads=True, ownership=("personal",)),
    _scaffold("sharepoint", "SharePoint", auth=("oauth2",), scopes=(("Sites.Read.All", "Read the sites and libraries you include"), ("offline_access", "Keep syncing")),
              source_types=("site", "library"), hosts=_GRAPH),
    _scaffold("onedrive", "OneDrive", auth=("oauth2",), scopes=(("Files.Read.All", "Read the folders you include"), ("offline_access", "Keep syncing")),
              source_types=("folder",), hosts=_GRAPH),
    _scaffold("notion", "Notion", auth=("oauth2",), scopes=(("read_content", "Read the pages and databases shared with the integration"),),
              source_types=("page", "database"), hosts=("api.notion.com",), acl="visibility_only",
              notes="Notion grants access per page shared with the integration, not by OAuth scope."),
    _scaffold("confluence", "Confluence", auth=("oauth2",),
              scopes=(("read:confluence-content.all", "Read pages and comments of the spaces you include"), ("read:confluence-space.summary", "List spaces"),
                      ("offline_access", "Keep syncing")), source_types=("space",), hosts=("api.atlassian.com", "auth.atlassian.com"), modes=("pull", "webhook")),
    _scaffold("jira", "Jira", auth=("oauth2",),
              scopes=(("read:jira-work", "Read issues and comments of the projects you include"), ("read:jira-user", "Resolve authors"), ("offline_access", "Keep syncing")),
              source_types=("project",), hosts=("api.atlassian.com", "auth.atlassian.com"), modes=("pull", "webhook"), deletes="webhook", threads=True),
    _scaffold("linear", "Linear", auth=("oauth2", "pat"), scopes=(("read", "Read issues and comments of the teams you include"),),
              source_types=("team",), hosts=("api.linear.app",), modes=("pull", "webhook"), deletes="webhook", threads=True),
    _scaffold("salesforce", "Salesforce", auth=("oauth2",), scopes=(("api", "Read the objects you include (accounts, cases, opportunities)"), ("refresh_token", "Keep syncing")),
              source_types=("object",), hosts=("login.salesforce.com",), acl="visibility_only",
              notes="Record-level sharing rules are not yet mapped; restricted objects will be ingested only with an explicit owner opt-in."),
    _scaffold("postgresql", "PostgreSQL (read-only)", auth=("pat",), scopes=(("SELECT on chosen tables", "A read-only database role limited to the tables you include"),),
              source_types=("table", "view"), hosts=(), acl="none", ownership=("org",),
              notes="Connects with a dedicated read-only role; rows become records only through an explicit column mapping."),
    _scaffold("mysql", "MySQL (read-only)", auth=("pat",), scopes=(("SELECT on chosen tables", "A read-only database user limited to the tables you include"),),
              source_types=("table", "view"), hosts=(), acl="none", ownership=("org",)),
    _scaffold("s3", "Amazon S3", auth=("pat",), scopes=(("s3:ListBucket, s3:GetObject", "Read the buckets and prefixes you include"),),
              source_types=("bucket", "prefix"), hosts=("s3.amazonaws.com",), acl="none", ownership=("org",)),
)
