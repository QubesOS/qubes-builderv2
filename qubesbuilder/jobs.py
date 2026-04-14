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
from dataclasses import dataclass
from graphlib import TopologicalSorter
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
        graph = {}
        for job in self.jobs:
            seen = set()
            deps = []

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
        ts = TopologicalSorter(graph)
        topo = list(ts.static_order())
        topo_index = {id(j): i for i, j in enumerate(topo)}

        # Sort by stage position (known stages) or topological index (unknown
        # stages like init-cache). Unknown stages are placed just before the
        # first known stage that depends on them.
        def sort_key(j):
            known = j.stage in stage_order
            if known:
                return (stage_order[j.stage], topo_index[id(j)])
            return (topo_index[id(j)] / (len(topo) + 1), topo_index[id(j)])

        return sorted(topo, key=sort_key)


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
