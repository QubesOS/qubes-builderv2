# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2026 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later
import heapq
from dataclasses import dataclass
from typing import Optional, List

from qubesbuilder.common import STAGES
from qubesbuilder.component import QubesComponent
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.exc import QubesBuilderError, ConfigError, ComponentError
from qubesbuilder.plugins import (
    JobReference,
    JobDependency,
    ComponentDependency,
    Plugin,
    PluginError,
)
from qubesbuilder.template import QubesTemplate


class PipelineError(QubesBuilderError):
    pass


@dataclass(frozen=True)
class JobKey:
    plugin_name: str
    component: Optional[str]
    dist: Optional[str]
    template: Optional[str]
    stage: str

    @classmethod
    def from_job(cls, job: Plugin) -> "JobKey":
        return cls(
            plugin_name=job.name,
            component=job.component.name if job.component else None,
            dist=job.dist.distribution if job.dist else None,
            template=str(job.template) if job.template else None,
            stage=job.stage,
        )


class Pipeline:
    def __init__(self):
        self.jobs: List[Plugin] = []
        self.by_key: dict = {}
        self.by_ref: dict = {}

    def add(self, ref: JobReference, job: Plugin):
        key = JobKey.from_job(job)
        if key in self.by_key:
            return
        self.by_key[key] = job
        self.by_ref[ref] = job
        self.jobs.append(job)

    def get(self, ref: JobReference) -> Optional[Plugin]:
        return self.by_ref.get(ref)

    def __contains__(self, ref: JobReference) -> bool:
        return ref in self.by_ref

    def __iter__(self):
        return iter(self.jobs)

    def __len__(self):
        return len(self.jobs)

    def build_graph(self, config) -> dict:
        graph: dict = {}
        seen_per_job: dict = {}
        for job in self.jobs:
            seen: set = set()
            deps: list = []

            for dep in getattr(job, "dependencies", []):
                if isinstance(dep, JobDependency):
                    dep_ref = JobReference(
                        dep.reference.component,
                        dep.reference.dist,
                        dep.reference.template,
                        dep.reference.stage,
                        None,
                    )
                    dep_job = self.by_ref.get(dep_ref)
                    if (
                        dep_job
                        and dep_job is not job
                        and id(dep_job) not in seen
                    ):
                        seen.add(id(dep_job))
                        deps.append(dep_job)
                elif isinstance(dep, ComponentDependency):
                    dep_ref = JobReference(
                        dep.reference, None, None, "fetch", None
                    )
                    dep_job = self.by_ref.get(dep_ref)
                    if dep_job and id(dep_job) not in seen:
                        seen.add(id(dep_job))
                        deps.append(dep_job)

            graph[job] = deps
            seen_per_job[id(job)] = seen

        # Implicit "previous stage" edges: when an explicit dep chain is
        # broken (e.g. publish -> sign(missing) -> build), we still want
        # later stages to run after earlier ones for the same target.
        # Chain by (dist, template) so dist-only jobs (createrepo etc.)
        # also run after per-component jobs at the same dist.
        stage_order = {s: i for i, s in enumerate(STAGES)}
        by_target: dict = {}
        for job in self.jobs:
            if job.stage not in stage_order:
                continue
            target = (
                job.dist.distribution if job.dist else None,
                str(job.template) if job.template else None,
            )
            by_target.setdefault(target, []).append(job)

        for target_jobs in by_target.values():
            target_jobs.sort(key=lambda j: stage_order[j.stage])
            for i, curr in enumerate(target_jobs):
                for prev in target_jobs[:i]:
                    if prev.stage == curr.stage:
                        continue
                    if id(prev) in seen_per_job[id(curr)]:
                        continue
                    seen_per_job[id(curr)].add(id(prev))
                    graph[curr].append(prev)

        return graph

    def validate(self, config):
        graph = self.build_graph(config)
        visited = set()
        path = set()

        def dfs(node):
            if node in path:
                raise PipelineError(
                    f"{node}: cycle detected in job dependencies"
                )
            if node in visited:
                return
            path.add(node)
            for dep in graph.get(node, []):
                dfs(dep)
            path.discard(node)
            visited.add(node)

        for job in self.jobs:
            dfs(job)

    def sorted_jobs(self, config) -> List[Plugin]:
        stage_order = {s: i for i, s in enumerate(STAGES)}
        graph = self.build_graph(config)

        # Precompute declaration-order rank so that within the same stage,
        # components run in the order they are listed in the config. This
        # ensures that a component listed last (e.g. installer-qubes-os-windows-tools)
        # runs after components it implicitly depends on (e.g. all other Windows
        # components whose artifacts must be in the local repo).
        all_components = config.get_components()
        comp_rank = {c.name: i for i, c in enumerate(all_components)}

        # Kahn's algorithm with stage-based priority: among jobs whose
        # dependencies are satisfied, pick the one with the lowest stage
        # index first. Within the same stage, honour component declaration order.
        # https://www.geeksforgeeks.org/dsa/lexicographically-smallest-topological-ordering/
        dependents: dict = {job: [] for job in graph}
        in_degree: dict = {job: 0 for job in graph}
        for job, deps in graph.items():
            for dep in deps:
                dependents[dep].append(job)
                in_degree[job] += 1

        def priority(j):
            # Unknown stages (e.g. init-cache) sit just after fetch so
            # chroot setup happens before prep but after source fetch.
            stage_pri = stage_order.get(j.stage, 0) + (
                0.5 if j.stage not in stage_order else 0
            )
            comp_pri = comp_rank.get(j.component.name, 0) if j.component else 0
            return (stage_pri, comp_pri)

        counter = 0
        heap: list = []
        for job, deg in in_degree.items():
            if deg == 0:
                heapq.heappush(heap, (priority(job), counter, job))
                counter += 1

        result: List[Plugin] = []
        while heap:
            _, _, job = heapq.heappop(heap)
            result.append(job)
            for d in dependents[job]:
                in_degree[d] -= 1
                if in_degree[d] == 0:
                    heapq.heappush(heap, (priority(d), counter, d))
                    counter += 1

        if len(result) != len(graph):
            raise PipelineError("cycle detected in job dependencies")

        return result


class JobFactory:
    def __init__(self, config):
        self.config = config
        manager = config.get_plugin_manager()
        self.plugins = sorted(manager.get_plugins(), key=lambda p: p.priority)

    def instantiate(self, ref: JobReference) -> Optional[Plugin]:
        config = self.config
        for plugin_cls in self.plugins:
            if not plugin_cls.matches(
                config=config,
                stage=ref.stage,
                component=ref.component,
                dist=ref.dist,
                template=ref.template,
            ):
                continue
            kwargs = {"config": config, "stage": ref.stage}
            if ref.component is not None:
                kwargs["component"] = ref.component
            if ref.dist is not None:
                kwargs["dist"] = ref.dist
            if ref.template is not None:
                kwargs["template"] = ref.template
            try:
                job = plugin_cls(**kwargs)
                if ref.component and ref.dist:
                    job.dependencies += config.get_needs(
                        component=ref.component, dist=ref.dist, stage=ref.stage
                    )
                # Drop the job if the component has no packages for this
                # distribution (e.g. a Windows component with a Debian dist,
                # or sources not yet fetched).
                if (
                    ref.component
                    and ref.dist
                    and not job.has_component_packages(ref.stage)
                ):
                    return None
            except (PluginError, ConfigError, ComponentError):
                # Source not fetched yet, or dist not in config; skip.
                return None
            return job
        return None

    def add_job(self, pipeline: Pipeline, ref: JobReference):
        if ref in pipeline:
            return pipeline.get(ref)
        job = self.instantiate(ref)
        if job is None:
            return None
        pipeline.add(ref, job)
        for dep in getattr(job, "dependencies", []):
            if isinstance(dep, JobDependency):
                # Clear build: we resolve by job, not by artifact.
                dep_ref = JobReference(
                    dep.reference.component,
                    dep.reference.dist,
                    dep.reference.template,
                    dep.reference.stage,
                    None,
                )
                self.add_job(pipeline, dep_ref)
            elif isinstance(dep, ComponentDependency):
                comp_fetch_ref = JobReference(
                    component=self.config.get_components([dep.reference])[0],
                    dist=None,
                    template=None,
                    stage="fetch",
                    build=None,
                )
                self.add_job(pipeline, comp_fetch_ref)
        return job

    def create(
        self,
        components: List[QubesComponent],
        distributions: List[QubesDistribution],
        templates: List[QubesTemplate],
        stages: List[str],
    ) -> Pipeline:
        pipeline = Pipeline()

        for stage in stages:
            for dist in distributions:
                for comp in components:
                    self.add_job(
                        pipeline,
                        JobReference(comp, dist, None, stage, None),
                    )
            for comp in components:
                self.add_job(
                    pipeline,
                    JobReference(comp, None, None, stage, None),
                )
            for dist in distributions:
                self.add_job(
                    pipeline,
                    JobReference(None, dist, None, stage, None),
                )
            for tmpl in templates:
                self.add_job(
                    pipeline,
                    JobReference(None, None, tmpl, stage, None),
                )

        return pipeline
