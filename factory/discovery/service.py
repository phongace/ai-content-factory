"""Discovery service: find/record references, rank them, analyze one into an opportunity, then hand a ContentBrief
to the existing Factory (create_project + plan). No second production pipeline."""

from __future__ import annotations

from ..config import Settings, get_settings
from ..pipeline import Factory
from ..planner import ContentPlanner
from ..schema import MAX_TOTAL, MIN_TOTAL, ContentBrief, Project
from .analyzer import OpportunityAnalyzer, get_analyzer
from .formats import get_format
from .models import ContentOpportunity, DiscoveryCandidate, OpportunityScores
from .scoring import rank, score_candidate
from .store import DiscoveryStore, new_id
from .youtube import NOT_CONFIGURED, DiscoveryError, YouTubeDiscovery, YouTubeVideo, platform_of, video_id_from_url


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def youtube_candidate_id(video_id: str) -> str:
    """Stable per video (a repeated search updates the record). Hex because ids are case-sensitive and the macOS
    filesystem is not."""
    return f"yt-{video_id.encode().hex()}"


class Discovery:
    def __init__(self, factory: Factory | None = None, settings: Settings | None = None, youtube_session=None):
        self.s = settings or (factory.s if factory else get_settings())
        self.factory = factory or Factory(self.s)
        self.store = DiscoveryStore(
            self.s.discovery_dir, self.s.opportunities_dir, self.s.patterns_dir, self.s.discovery_cache_hours
        )
        self._yt_session = youtube_session

    @property
    def youtube_configured(self) -> bool:
        return bool(self.s.youtube_api_key)

    def _youtube(self) -> YouTubeDiscovery:
        return YouTubeDiscovery(self.s.youtube_api_key, self._yt_session)

    @staticmethod
    def _apply_video(c: DiscoveryCandidate, v: YouTubeVideo) -> None:
        c.url = c.url or v.url
        c.platform, c.external_id = "youtube", v.video_id
        c.title = c.title or v.title
        c.description = v.description
        c.author, c.author_id = v.channel_title, v.channel_id
        c.published_at, c.duration_s = v.published_at, v.duration_s
        c.metrics = v.metrics()
        c.enrich_error = None

    # ------------------------------------------------------------ mode A: manual URL
    def add_url(
        self,
        url: str,
        title: str | None = None,
        transcript: str | None = None,
        notes: str | None = None,
        niche: str | None = None,
    ) -> DiscoveryCandidate:
        """Always saves the URL. A YouTube URL is enriched with public metadata when YOUTUBE_API_KEY is set; any
        failure is recorded on the candidate instead of failing the workflow. Other platforms are never fetched."""
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("paste a full http(s) URL")
        platform = platform_of(url)
        c = DiscoveryCandidate(
            id=new_id("url", f"{platform} {title or ''}"),
            source="manual_url",
            url=url,
            platform=platform,
            title=_clean(title),
            transcript=_clean(transcript),
            notes=_clean(notes),
            niche=_clean(niche),
        )
        vid = video_id_from_url(url) if platform == "youtube" else None
        if vid:
            c.external_id = vid
            try:
                found = self._youtube().videos([vid])
                if found:
                    self._apply_video(c, found[0])
                else:
                    c.enrich_error = "video not found or not public; add title/transcript manually"
            except DiscoveryError as e:
                c.enrich_error = str(e)
        elif platform != "youtube":
            c.enrich_error = f"no metadata fetch for {platform} links; add title/transcript/notes manually"
        else:
            c.enrich_error = "not a YouTube video link; add title/transcript manually"
        c.score = score_candidate(c)
        return self.store.save_candidate(c)

    # ------------------------------------------------------------ mode B: manual content
    def add_content(
        self,
        topic: str | None = None,
        title: str | None = None,
        transcript: str | None = None,
        notes: str | None = None,
        url: str | None = None,
        niche: str | None = None,
    ) -> DiscoveryCandidate:
        topic, title, transcript = _clean(topic), _clean(title), _clean(transcript)
        if not (topic or title or transcript):
            raise ValueError("enter at least a topic, a title or a transcript")
        url = _clean(url)
        c = DiscoveryCandidate(
            id=new_id("txt", topic or title or "content"),
            source="manual_content",
            url=url,
            platform=platform_of(url) if url else None,
            topic=topic,
            title=title,
            transcript=transcript,
            notes=_clean(notes),
            niche=_clean(niche),
        )
        return self.store.save_candidate(c)

    def edit_candidate(
        self,
        cid: str,
        title: str | None = None,
        transcript: str | None = None,
        notes: str | None = None,
        topic: str | None = None,
    ) -> DiscoveryCandidate:
        def fn(c: DiscoveryCandidate) -> None:
            for field, value in (("title", title), ("transcript", transcript), ("notes", notes), ("topic", topic)):
                if value is not None:
                    setattr(c, field, _clean(value))

        return self.store.update_candidate(cid, fn)

    # ------------------------------------------------------------ mode C: YouTube search
    def search_youtube(
        self,
        niche: str,
        keywords: str = "",
        lookback_days: int = 30,
        max_results: int = 20,
        language: str | None = None,
    ) -> tuple[list[DiscoveryCandidate], bool]:
        """Ranked candidates and whether they came from the local cache (no API call)."""
        if not self.youtube_configured:
            raise DiscoveryError(NOT_CONFIGURED)
        query = " ".join(x for x in (niche.strip(), keywords.strip()) if x)
        params = {"q": query, "days": lookback_days, "max": max_results, "lang": language or ""}
        cached = self.store.cached_search(params)
        if cached is not None:
            found = [c for cid in cached if (c := self.store.find_candidate(cid))]
            return rank(found), True
        videos = self._youtube().search(query, lookback_days, max_results, language)
        out = []
        for v in videos:
            cid = youtube_candidate_id(v.video_id)
            c = self.store.find_candidate(cid) or DiscoveryCandidate(id=cid, source="youtube")
            self._apply_video(c, v)
            c.niche, c.query = _clean(niche), query
            c.score = score_candidate(c)
            out.append(self.store.save_candidate(c))
        self.store.cache_search(params, [c.id for c in out])
        return rank(out), False

    def candidates(self) -> list[DiscoveryCandidate]:
        return rank(self.store.list_candidates())

    # ------------------------------------------------------------ analyze
    def analyze(
        self,
        cid: str,
        language: str = "vi",
        niche: str | None = None,
        analyzer: OpportunityAnalyzer | None = None,
    ) -> ContentOpportunity:
        c = self.store.get_candidate(cid)
        if not (c.title or c.topic or c.transcript or c.description or c.notes):
            raise ValueError("this reference has no title, topic, transcript or notes yet: add one before analyzing")
        result = (analyzer or get_analyzer(self.s)).analyze(c, language, niche)
        d = result.draft
        best = max(range(len(d.hooks)), key=lambda i: d.hooks[i].overall)
        scores = OpportunityScores(
            viral_signal=c.score.viral_score if c.score else None,
            hook_potential=d.hooks[best].overall,
            novelty=d.novelty,
            channel_fit=d.channel_fit,
            visual_feasibility=d.visual_feasibility,
            production_efficiency=d.production_efficiency,
        )
        scores.recompute()
        o = ContentOpportunity(
            id=new_id("opp", d.topic),
            candidate_id=c.id,
            analyzer=result.provider,
            topic=d.topic,
            angle=d.angle,
            hook_pattern=d.hook_pattern,
            hooks=d.hooks,
            selected_hook=best,
            format_id=d.format_id,
            analyzed_format_id=d.format_id,
            story_structure=d.story_structure,
            why_it_may_work=d.why_it_may_work,
            scores=scores,
            language=language,
            cost_usd=result.cost_usd,
        )
        return self.store.save_opportunity(o)

    def choose(self, oid: str, hook_index: int | None = None, format_id: str | None = None) -> ContentOpportunity:
        def fn(o: ContentOpportunity) -> None:
            if hook_index is not None:
                if not 0 <= hook_index < len(o.hooks):
                    raise ValueError(f"hook index must be 0-{len(o.hooks) - 1}")
                o.selected_hook = hook_index
            if format_id is not None:
                o.format_id = get_format(format_id).id

        return self.store.update_opportunity(oid, fn)

    def save_pattern(self, oid: str) -> ContentOpportunity:
        o = self.store.get_opportunity(oid)
        self.store.save_pattern(o)

        def fn(o: ContentOpportunity) -> None:
            o.saved_pattern = True

        return self.store.update_opportunity(oid, fn)

    # ------------------------------------------------------------ hand-off to the existing pipeline
    def brief(self, o: ContentOpportunity) -> ContentBrief:
        fmt = get_format(o.format_id)
        c = self.store.find_candidate(o.candidate_id)
        # the analyzer's beats follow its own pick; a user override of the format brings that format's beats
        beats = o.story_structure if o.format_id == (o.analyzed_format_id or o.format_id) else fmt.story_beats
        return ContentBrief(
            topic=o.topic,
            angle=o.angle,
            hook=o.hook.text,
            hook_pattern=o.hook_pattern,
            format_id=fmt.id,
            story_beats=list(beats),
            pacing=fmt.pacing,
            source_discovery_id=o.candidate_id,
            source_opportunity_id=o.id,
            reference_url=c.url if c else None,
        )

    def generate_video(
        self,
        oid: str,
        target_duration: int | None = None,
        visual_style: str = "cinematic, realistic, soft natural light",
        planner: ContentPlanner | None = None,
        hook_index: int | None = None,
        format_id: str | None = None,
    ) -> Project:
        """Opportunity -> project with a planned storyboard, through the normal Factory.create_project + plan.
        `hook_index` / `format_id` override the opportunity's current choice first (and are saved on it)."""
        o = self.choose(oid, hook_index, format_id)
        fmt = get_format(o.format_id)
        duration = target_duration or round(sum(fmt.target_duration) / 2)
        duration = max(MIN_TOTAL, min(MAX_TOTAL, duration))
        p = self.factory.create_project(o.topic, duration, o.language, visual_style, brief=self.brief(o))

        def link(o: ContentOpportunity) -> None:
            o.project_ids.append(p.id)

        self.store.update_opportunity(oid, link)
        self.factory.store.log(p.id, f"from opportunity {oid} (candidate {o.candidate_id}, format {fmt.id})")
        self.factory.plan(p.id, planner)
        return self.factory.store.get(p.id)
