"""
Domain Hierarchy for Sharded Memory System.

Defines the complete taxonomy of domains and subdomains with their
cross-references. This is the knowledge graph schema for memory organization.

Domain Hierarchy:

    PERSONALITY (Mental State)
    ├── Typing Style
    ├── Personality Traits
    ├── Mental Health Signals
    ├── Communication Patterns
    └── Emotional Baseline
        ↔ References: PREFERENCES (influences), CURRENT_DATA (reflects)

    PREFERENCES
    ├── Code Preferences
    ├── Music Preferences
    ├── Environmental Preferences
    ├── Content Preferences
    ├── Workflow Preferences
    ├── Food Preferences
    └── Aesthetic Preferences
        ↔ References: PERSONALITY (derived from), CURRENT_DATA (active prefs)

    LIFE_ADVICE
    ├── Advice Given
    ├── Strengths
    ├── Weaknesses
    ├── Resilience Patterns
    ├── Growth Areas
    └── Coping Mechanisms
        ↔ References: PERSONALITY (based on), GOALS (supports)

    GOALS
    ├── Short-term Goals
    ├── Long-term Goals
    ├── Deadlines
    ├── Milestones
    └── Aspirations
        ↔ References: LIFE_ADVICE (informed by), CURRENT_DATA (active goals)

    CURRENT_DATA (Last ~30 days)
    ├── Recent Topics
    ├── Active Projects
    ├── Current Mood
    ├── Pending Tasks
    └── Recent Interactions
        ↔ References: ALL DOMAINS (provides context window)

    RELATIONSHIPS
    ├── Family
    ├── Friends
    ├── Colleagues
    ├── Acquaintances
    └── Pets
        ↔ References: LIFE_ADVICE (relationship advice), CURRENT_DATA (recent)

    KNOWLEDGE
    ├── Expertise Areas
    ├── Learning Interests
    ├── Education
    └── Skills
        ↔ References: GOALS (learning goals), PREFERENCES (interests)

    HISTORICAL
    ├── Life Events
    ├── Past Experiences
    └── Memories
        ↔ References: ALL DOMAINS (source of patterns)
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from .data_types import (
    Domain,
    DomainEdge,
    DomainType,
    HashPointer,
    Subdomain,
    SubdomainType,
)


# =============================================================================
# DOMAIN HIERARCHY DEFINITION
# =============================================================================

@dataclass
class SubdomainDefinition:
    """Definition of a subdomain in the hierarchy."""
    subdomain_type: SubdomainType
    name: str
    description: str
    icon: str = ""


@dataclass
class DomainDefinition:
    """Definition of a domain in the hierarchy."""
    domain_type: DomainType
    name: str
    description: str
    icon: str = ""
    color: str = ""
    subdomains: list[SubdomainDefinition] = field(default_factory=list)
    related_domains: list[tuple[DomainType, str]] = field(default_factory=list)


# Complete domain hierarchy definition
DOMAIN_HIERARCHY: dict[DomainType, DomainDefinition] = {

    # =========================================================================
    # PERSONALITY & MENTAL STATE
    # =========================================================================
    DomainType.PERSONALITY: DomainDefinition(
        domain_type=DomainType.PERSONALITY,
        name="Personality & Mental State",
        description="User's personality traits, typing patterns, and mental state signals",
        icon="brain",
        color="#9B59B6",
        subdomains=[
            SubdomainDefinition(
                subdomain_type=SubdomainType.TYPING_STYLE,
                name="Typing Style",
                description="How the user types: formality, emoji usage, punctuation patterns",
                icon="keyboard",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.PERSONALITY_TRAITS,
                name="Personality Traits",
                description="Big Five traits, MBTI indicators, behavioral patterns",
                icon="fingerprint",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.MENTAL_HEALTH_SIGNALS,
                name="Mental Health Signals",
                description="Stress indicators, mood patterns, wellbeing signals",
                icon="heart-pulse",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.COMMUNICATION_PATTERNS,
                name="Communication Patterns",
                description="How user prefers to communicate, verbosity, directness",
                icon="message-circle",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.EMOTIONAL_BASELINE,
                name="Emotional Baseline",
                description="Typical emotional state and reactions",
                icon="smile",
            ),
        ],
        related_domains=[
            (DomainType.PREFERENCES, "influences"),
            (DomainType.CURRENT_DATA, "reflects_in"),
            (DomainType.LIFE_ADVICE, "informs"),
        ],
    ),

    # =========================================================================
    # PREFERENCES
    # =========================================================================
    DomainType.PREFERENCES: DomainDefinition(
        domain_type=DomainType.PREFERENCES,
        name="Preferences",
        description="User preferences across various domains",
        icon="settings",
        color="#3498DB",
        subdomains=[
            SubdomainDefinition(
                subdomain_type=SubdomainType.CODE_PREFERENCES,
                name="Code Preferences",
                description="Preferred languages, frameworks, indentation, naming conventions",
                icon="code",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.MUSIC_PREFERENCES,
                name="Music Preferences",
                description="Favorite genres, artists, listening habits",
                icon="music",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.ENVIRONMENTAL_PREFERENCES,
                name="Environmental Preferences",
                description="Lighting, noise level, temperature, workspace setup",
                icon="sun",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.CONTENT_PREFERENCES,
                name="Content Preferences",
                description="Books, movies, media consumption preferences",
                icon="book-open",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.WORKFLOW_PREFERENCES,
                name="Workflow Preferences",
                description="Work style, tool preferences, productivity patterns",
                icon="workflow",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.FOOD_PREFERENCES,
                name="Food Preferences",
                description="Dietary preferences, favorite cuisines, restrictions",
                icon="utensils",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.AESTHETIC_PREFERENCES,
                name="Aesthetic Preferences",
                description="Visual style, design preferences, color preferences",
                icon="palette",
            ),
        ],
        related_domains=[
            (DomainType.PERSONALITY, "derived_from"),
            (DomainType.CURRENT_DATA, "active_preferences"),
            (DomainType.KNOWLEDGE, "expertise_preferences"),
        ],
    ),

    # =========================================================================
    # LIFE ADVICE
    # =========================================================================
    DomainType.LIFE_ADVICE: DomainDefinition(
        domain_type=DomainType.LIFE_ADVICE,
        name="Life Advice & Coaching",
        description="Advice given to user, strengths, weaknesses, growth patterns",
        icon="compass",
        color="#E74C3C",
        subdomains=[
            SubdomainDefinition(
                subdomain_type=SubdomainType.ADVICE_GIVEN,
                name="Advice Given",
                description="Record of advice and coaching provided to user",
                icon="message-square",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.STRENGTHS,
                name="Strengths",
                description="User's identified strengths and capabilities",
                icon="trending-up",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.WEAKNESSES,
                name="Weaknesses",
                description="Areas where user struggles or needs improvement",
                icon="trending-down",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.RESILIENCE_PATTERNS,
                name="Resilience Patterns",
                description="How user handles adversity and bounces back",
                icon="shield",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.GROWTH_AREAS,
                name="Growth Areas",
                description="Active areas of personal development",
                icon="sprout",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.COPING_MECHANISMS,
                name="Coping Mechanisms",
                description="How user deals with stress and challenges",
                icon="umbrella",
            ),
        ],
        related_domains=[
            (DomainType.PERSONALITY, "based_on"),
            (DomainType.GOALS, "supports"),
            (DomainType.RELATIONSHIPS, "relationship_advice"),
        ],
    ),

    # =========================================================================
    # GOALS
    # =========================================================================
    DomainType.GOALS: DomainDefinition(
        domain_type=DomainType.GOALS,
        name="Goals & Objectives",
        description="User's short-term and long-term goals with timelines",
        icon="target",
        color="#2ECC71",
        subdomains=[
            SubdomainDefinition(
                subdomain_type=SubdomainType.SHORT_TERM_GOALS,
                name="Short-term Goals",
                description="Goals for the next days to weeks",
                icon="clock",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.LONG_TERM_GOALS,
                name="Long-term Goals",
                description="Goals with months to years horizon",
                icon="calendar",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.DEADLINES,
                name="Deadlines",
                description="Specific dates and time-bound commitments",
                icon="alarm-clock",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.MILESTONES,
                name="Milestones",
                description="Key achievements and progress markers",
                icon="flag",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.ASPIRATIONS,
                name="Aspirations",
                description="Dreams and long-term life aspirations",
                icon="star",
            ),
        ],
        related_domains=[
            (DomainType.LIFE_ADVICE, "informed_by"),
            (DomainType.CURRENT_DATA, "active_goals"),
            (DomainType.KNOWLEDGE, "learning_goals"),
        ],
    ),

    # =========================================================================
    # CURRENT DATA (Last ~30 days)
    # =========================================================================
    DomainType.CURRENT_DATA: DomainDefinition(
        domain_type=DomainType.CURRENT_DATA,
        name="Current Data",
        description="Recent information from the last ~30 days",
        icon="activity",
        color="#F39C12",
        subdomains=[
            SubdomainDefinition(
                subdomain_type=SubdomainType.RECENT_TOPICS,
                name="Recent Topics",
                description="Topics discussed in recent conversations",
                icon="hash",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.ACTIVE_PROJECTS,
                name="Active Projects",
                description="Projects user is currently working on",
                icon="folder",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.CURRENT_MOOD,
                name="Current Mood",
                description="Recent emotional state and mood trends",
                icon="smile",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.PENDING_TASKS,
                name="Pending Tasks",
                description="Tasks and to-dos waiting to be completed",
                icon="check-square",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.RECENT_INTERACTIONS,
                name="Recent Interactions",
                description="Recent conversation summaries and interactions",
                icon="message-circle",
            ),
        ],
        related_domains=[
            # Current data references ALL other domains
            (DomainType.PERSONALITY, "reflects"),
            (DomainType.PREFERENCES, "active_preferences"),
            (DomainType.LIFE_ADVICE, "recent_advice"),
            (DomainType.GOALS, "active_goals"),
            (DomainType.RELATIONSHIPS, "recent_interactions"),
            (DomainType.KNOWLEDGE, "recent_learning"),
            (DomainType.HISTORICAL, "recent_events"),
        ],
    ),

    # =========================================================================
    # RELATIONSHIPS
    # =========================================================================
    DomainType.RELATIONSHIPS: DomainDefinition(
        domain_type=DomainType.RELATIONSHIPS,
        name="Relationships",
        description="User's social connections and relationships",
        icon="users",
        color="#1ABC9C",
        subdomains=[
            SubdomainDefinition(
                subdomain_type=SubdomainType.FAMILY,
                name="Family",
                description="Family members and family relationships",
                icon="home",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.FRIENDS,
                name="Friends",
                description="Friendships and close connections",
                icon="user-plus",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.COLLEAGUES,
                name="Colleagues",
                description="Work relationships and professional contacts",
                icon="briefcase",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.ACQUAINTANCES,
                name="Acquaintances",
                description="Casual connections and known people",
                icon="user",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.PETS,
                name="Pets",
                description="Pets and animal companions",
                icon="paw-print",
            ),
        ],
        related_domains=[
            (DomainType.LIFE_ADVICE, "relationship_guidance"),
            (DomainType.CURRENT_DATA, "recent_interactions"),
            (DomainType.HISTORICAL, "relationship_history"),
        ],
    ),

    # =========================================================================
    # KNOWLEDGE
    # =========================================================================
    DomainType.KNOWLEDGE: DomainDefinition(
        domain_type=DomainType.KNOWLEDGE,
        name="Knowledge & Expertise",
        description="User's knowledge, skills, and learning interests",
        icon="book",
        color="#8E44AD",
        subdomains=[
            SubdomainDefinition(
                subdomain_type=SubdomainType.EXPERTISE_AREAS,
                name="Expertise Areas",
                description="Domains where user has deep knowledge",
                icon="award",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.LEARNING_INTERESTS,
                name="Learning Interests",
                description="Topics user wants to learn more about",
                icon="book-open",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.EDUCATION,
                name="Education",
                description="Formal education and training history",
                icon="graduation-cap",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.SKILLS,
                name="Skills",
                description="Practical skills and capabilities",
                icon="tool",
            ),
        ],
        related_domains=[
            (DomainType.GOALS, "learning_goals"),
            (DomainType.PREFERENCES, "knowledge_preferences"),
            (DomainType.CURRENT_DATA, "recent_learning"),
        ],
    ),

    # =========================================================================
    # HISTORICAL
    # =========================================================================
    DomainType.HISTORICAL: DomainDefinition(
        domain_type=DomainType.HISTORICAL,
        name="Historical Data",
        description="Past events, experiences, and long-term memories",
        icon="clock",
        color="#95A5A6",
        subdomains=[
            SubdomainDefinition(
                subdomain_type=SubdomainType.LIFE_EVENTS,
                name="Life Events",
                description="Significant life events and milestones",
                icon="calendar-check",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.PAST_EXPERIENCES,
                name="Past Experiences",
                description="Previous experiences that shaped the user",
                icon="archive",
            ),
            SubdomainDefinition(
                subdomain_type=SubdomainType.MEMORIES,
                name="Memories",
                description="Specific memories and stories shared",
                icon="image",
            ),
        ],
        related_domains=[
            # Historical connects to all domains as source of patterns
            (DomainType.PERSONALITY, "shaped_by"),
            (DomainType.LIFE_ADVICE, "lessons_learned"),
            (DomainType.RELATIONSHIPS, "relationship_history"),
            (DomainType.CURRENT_DATA, "provides_context"),
        ],
    ),
}


# =============================================================================
# SUBDOMAIN TO DOMAIN MAPPING
# =============================================================================

SUBDOMAIN_TO_DOMAIN: dict[SubdomainType, DomainType] = {
    # Personality
    SubdomainType.TYPING_STYLE: DomainType.PERSONALITY,
    SubdomainType.PERSONALITY_TRAITS: DomainType.PERSONALITY,
    SubdomainType.MENTAL_HEALTH_SIGNALS: DomainType.PERSONALITY,
    SubdomainType.COMMUNICATION_PATTERNS: DomainType.PERSONALITY,
    SubdomainType.EMOTIONAL_BASELINE: DomainType.PERSONALITY,

    # Preferences
    SubdomainType.CODE_PREFERENCES: DomainType.PREFERENCES,
    SubdomainType.MUSIC_PREFERENCES: DomainType.PREFERENCES,
    SubdomainType.ENVIRONMENTAL_PREFERENCES: DomainType.PREFERENCES,
    SubdomainType.CONTENT_PREFERENCES: DomainType.PREFERENCES,
    SubdomainType.WORKFLOW_PREFERENCES: DomainType.PREFERENCES,
    SubdomainType.FOOD_PREFERENCES: DomainType.PREFERENCES,
    SubdomainType.AESTHETIC_PREFERENCES: DomainType.PREFERENCES,

    # Life Advice
    SubdomainType.ADVICE_GIVEN: DomainType.LIFE_ADVICE,
    SubdomainType.STRENGTHS: DomainType.LIFE_ADVICE,
    SubdomainType.WEAKNESSES: DomainType.LIFE_ADVICE,
    SubdomainType.RESILIENCE_PATTERNS: DomainType.LIFE_ADVICE,
    SubdomainType.GROWTH_AREAS: DomainType.LIFE_ADVICE,
    SubdomainType.COPING_MECHANISMS: DomainType.LIFE_ADVICE,

    # Goals
    SubdomainType.SHORT_TERM_GOALS: DomainType.GOALS,
    SubdomainType.LONG_TERM_GOALS: DomainType.GOALS,
    SubdomainType.DEADLINES: DomainType.GOALS,
    SubdomainType.MILESTONES: DomainType.GOALS,
    SubdomainType.ASPIRATIONS: DomainType.GOALS,

    # Current Data
    SubdomainType.RECENT_TOPICS: DomainType.CURRENT_DATA,
    SubdomainType.ACTIVE_PROJECTS: DomainType.CURRENT_DATA,
    SubdomainType.CURRENT_MOOD: DomainType.CURRENT_DATA,
    SubdomainType.PENDING_TASKS: DomainType.CURRENT_DATA,
    SubdomainType.RECENT_INTERACTIONS: DomainType.CURRENT_DATA,

    # Relationships
    SubdomainType.FAMILY: DomainType.RELATIONSHIPS,
    SubdomainType.FRIENDS: DomainType.RELATIONSHIPS,
    SubdomainType.COLLEAGUES: DomainType.RELATIONSHIPS,
    SubdomainType.ACQUAINTANCES: DomainType.RELATIONSHIPS,
    SubdomainType.PETS: DomainType.RELATIONSHIPS,

    # Knowledge
    SubdomainType.EXPERTISE_AREAS: DomainType.KNOWLEDGE,
    SubdomainType.LEARNING_INTERESTS: DomainType.KNOWLEDGE,
    SubdomainType.EDUCATION: DomainType.KNOWLEDGE,
    SubdomainType.SKILLS: DomainType.KNOWLEDGE,

    # Historical
    SubdomainType.LIFE_EVENTS: DomainType.HISTORICAL,
    SubdomainType.PAST_EXPERIENCES: DomainType.HISTORICAL,
    SubdomainType.MEMORIES: DomainType.HISTORICAL,
}


# =============================================================================
# DOMAIN HIERARCHY MANAGER
# =============================================================================

class DomainHierarchyManager:
    """
    Manages the domain hierarchy and provides utilities for:
    - Creating domains/subdomains for a user
    - Setting up cross-domain references
    - Querying the hierarchy
    """

    def __init__(self):
        self._hierarchy = DOMAIN_HIERARCHY
        self._subdomain_to_domain = SUBDOMAIN_TO_DOMAIN

    def create_domains_for_user(self, user_id: str) -> list[Domain]:
        """Create all domains for a new user."""
        domains = []

        for domain_type, definition in self._hierarchy.items():
            domain = Domain(
                domain_id=f"{user_id}:domain:{domain_type.value}",
                domain_type=domain_type,
                user_id=user_id,
                name=definition.name,
                description=definition.description,
                icon=definition.icon,
                color=definition.color,
            )
            domains.append(domain)

        return domains

    def create_subdomains_for_user(self, user_id: str) -> list[Subdomain]:
        """Create all subdomains for a new user."""
        subdomains = []

        for domain_type, definition in self._hierarchy.items():
            parent_domain_id = f"{user_id}:domain:{domain_type.value}"

            for subdomain_def in definition.subdomains:
                subdomain = Subdomain(
                    subdomain_id=f"{user_id}:subdomain:{subdomain_def.subdomain_type.value}",
                    subdomain_type=subdomain_def.subdomain_type,
                    parent_domain_id=parent_domain_id,
                    user_id=user_id,
                    name=subdomain_def.name,
                    description=subdomain_def.description,
                    icon=subdomain_def.icon,
                )
                subdomains.append(subdomain)

        return subdomains

    def create_domain_edges(self, user_id: str) -> list[DomainEdge]:
        """Create cross-domain edges based on the hierarchy definition."""
        edges = []

        for domain_type, definition in self._hierarchy.items():
            source_domain_id = f"{user_id}:domain:{domain_type.value}"

            for related_domain_type, relation in definition.related_domains:
                target_domain_id = f"{user_id}:domain:{related_domain_type.value}"

                edge = DomainEdge(
                    edge_id=f"{source_domain_id}--{relation}--{target_domain_id}",
                    source_domain_id=source_domain_id,
                    target_domain_id=target_domain_id,
                    relation_type=relation,
                    bidirectional=True,
                )
                edges.append(edge)

        return edges

    def get_domain_type_for_subdomain(self, subdomain_type: SubdomainType) -> DomainType:
        """Get the parent domain type for a subdomain."""
        return self._subdomain_to_domain[subdomain_type]

    def get_subdomains_for_domain(self, domain_type: DomainType) -> list[SubdomainType]:
        """Get all subdomain types for a domain."""
        definition = self._hierarchy.get(domain_type)
        if not definition:
            return []
        return [sd.subdomain_type for sd in definition.subdomains]

    def get_related_domains(self, domain_type: DomainType) -> list[tuple[DomainType, str]]:
        """Get domains related to the given domain."""
        definition = self._hierarchy.get(domain_type)
        if not definition:
            return []
        return definition.related_domains

    def get_all_domain_types(self) -> list[DomainType]:
        """Get all domain types."""
        return list(self._hierarchy.keys())

    def get_all_subdomain_types(self) -> list[SubdomainType]:
        """Get all subdomain types."""
        return list(self._subdomain_to_domain.keys())


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_domain_by_type(domain_type: DomainType) -> DomainDefinition | None:
    """Get domain definition by type."""
    return DOMAIN_HIERARCHY.get(domain_type)


def get_subdomains_for_domain(domain_type: DomainType) -> list[SubdomainDefinition]:
    """Get subdomain definitions for a domain type."""
    definition = DOMAIN_HIERARCHY.get(domain_type)
    if not definition:
        return []
    return definition.subdomains


def get_parent_domain(subdomain_type: SubdomainType) -> DomainType:
    """Get the parent domain type for a subdomain type."""
    return SUBDOMAIN_TO_DOMAIN.get(subdomain_type)
