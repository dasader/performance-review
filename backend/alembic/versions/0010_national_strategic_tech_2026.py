"""국가전략기술 체계를 제1호 개정안(10대 분야 55개 중점기술)으로 전면 교체

기존 12대 분야 + 임의 세부기술 4건을 버리고 개정안 체계를 그대로 심는다.
분야·세부기술이 전부 새 id를 받으므로 기존 분석·보고서는 귀속될 곳이 없어 함께 지운다.

- 삭제: analysis_runs / analysis_papers / analyses / paper_extractions / subfields / fields
- 보존: papers (검색 결과 캐시 — 세부기술과 무관하게 paper_key로 재사용된다)
- paper_extractions는 subfield_id에 묶인 캐시라(uq_extraction) 세부기술을 교체하면
  어차피 히트하지 않는다. FK 때문에 남겨둘 수도 없어 함께 지운다.

검색식은 OpenAlex `title_and_abstract.search` 전용(영문 불리언). KCI용 국문 검색식은
키 갱신 후 별도로 채운다 — query_kci가 NULL이면 공통 검색식(영문)이 쓰인다.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


FIELDS = [
    ("인공지능", "ai"),
    ("첨단로봇·모빌리티", "robot-mobility"),
    ("차세대 보안·네트워크", "security-network"),
    ("반도체·디스플레이", "semiconductor-display"),
    ("첨단바이오", "bio"),
    ("차세대전지", "battery"),
    ("우주항공·해양", "space-ocean"),
    ("혁신·미래소재", "materials"),
    ("미래에너지·원자력", "energy-nuclear"),
    ("양자", "quantum"),
]

# (분야 slug, 중점기술명, OpenAlex 검색식)
SUBFIELDS = [
    ("ai", "AI 인프라 고도화",
     '("AI infrastructure" OR "AI data center" OR "GPU cluster" OR "distributed deep learning" '
     'OR "inference serving" OR "machine learning system") AND (scalability OR "energy efficiency" '
     'OR scheduling OR throughput)'),
    ("ai", "효율적 AI 학습 및 추론",
     '("model compression" OR quantization OR "knowledge distillation" OR pruning '
     'OR "parameter-efficient fine-tuning" OR "efficient inference" OR "efficient training") '
     'AND ("neural network" OR "language model" OR "deep learning")'),
    ("ai", "첨단 AI모델링·의사결정",
     '("large language model" OR "multimodal learning" OR "LLM agent" OR "AI agent" '
     'OR "artificial general intelligence" OR "chain-of-thought") AND (reasoning OR planning '
     'OR "decision making")'),
    ("ai", "안전·신뢰 AI",
     '"AI safety" OR "trustworthy AI" OR "explainable AI" OR "AI alignment" OR hallucination '
     'OR "adversarial robustness" OR "algorithmic fairness" OR "AI evaluation benchmark"'),
    ("ai", "버티컬 AI",
     '("domain-specific language model" OR "domain adaptation" OR "vertical AI" '
     'OR "retrieval-augmented generation" OR "foundation model") AND (medical OR legal '
     'OR industrial OR finance OR manufacturing OR scientific)'),

    ("robot-mobility", "로봇 부품·플랫폼",
     '(robot OR robotic) AND (actuator OR "force sensor" OR "tactile sensor" OR "robot platform" '
     'OR "motor controller" OR "compliant mechanism" OR "harmonic drive")'),
    ("robot-mobility", "로봇 지능기술",
     '(robot OR robotic) AND ("motion planning" OR manipulation OR grasping OR "embodied AI" '
     'OR "robot learning" OR SLAM OR "human-robot interaction" OR navigation)'),
    ("robot-mobility", "AI 제조",
     '("smart manufacturing" OR "digital twin" OR "industrial AI" OR "predictive maintenance" '
     'OR "smart factory" OR "process optimization") AND (manufacturing OR production OR industrial)'),
    ("robot-mobility", "자율주행 시스템",
     '("autonomous driving" OR "self-driving" OR "automated vehicle" OR "autonomous vehicle") '
     'AND (perception OR planning OR control OR safety OR validation OR "sensor fusion")'),

    ("security-network", "데이터·AI 보안",
     '("privacy-preserving" OR "federated learning" OR "differential privacy" '
     'OR "homomorphic encryption" OR "AI security" OR "adversarial attack" OR "data protection") '
     'AND (security OR privacy)'),
    ("security-network", "디지털 취약점 분석·침해대응",
     '"vulnerability detection" OR "intrusion detection" OR "malware analysis" '
     'OR "threat intelligence" OR "incident response" OR "software supply chain security" '
     'OR "penetration testing" OR fuzzing'),
    ("security-network", "산업보안·블록체인",
     'blockchain OR "distributed ledger" OR "smart contract" OR "zero-knowledge proof" '
     'OR "decentralized identity" OR "industrial control system security" OR "OT security"'),
    ("security-network", "6G",
     '("6G" OR "IMT-2030" OR "terahertz communication" OR "reconfigurable intelligent surface" '
     'OR "cell-free massive MIMO") AND (wireless OR communication OR network)'),
    ("security-network", "5G 고도화(5G-Adv)",
     '("5G-Advanced" OR "3GPP Release 18" OR "network slicing" OR "massive MIMO" '
     'OR "millimeter wave") AND ("5G" OR "mobile network" OR "cellular network")'),
    ("security-network", "위성통신",
     '"satellite communication" OR "low earth orbit satellite" OR "LEO satellite" '
     'OR "non-terrestrial network" OR "satellite constellation" OR "inter-satellite link"'),
    ("security-network", "AI-네트워크",
     '"AI-RAN" OR "O-RAN" OR "intelligent radio access network" OR "network automation" '
     'OR "self-organizing network" OR "intent-based networking" OR "AI-native network"'),
    ("security-network", "차세대 통신부품",
     '("RF front-end" OR "power amplifier" OR "phased array antenna" '
     'OR "RF integrated circuit" OR "millimeter wave component" OR "beamforming antenna") '
     'AND (wireless OR satellite OR communication OR RF)'),

    ("semiconductor-display", "차세대 메모리반도체",
     '"resistive random access memory" OR "phase change memory" OR "magnetoresistive memory" '
     'OR "ferroelectric memory" OR "emerging nonvolatile memory" OR "3D NAND" '
     'OR "high bandwidth memory" OR "compute-in-memory"'),
    ("semiconductor-display", "고성능·저전력 인공지능 반도체",
     '("AI accelerator" OR "neural processing unit" OR neuromorphic OR "in-memory computing" '
     'OR "deep learning accelerator" OR "systolic array") AND ("low power" OR energy '
     'OR efficiency OR chip OR architecture)'),
    ("semiconductor-display", "반도체 첨단패키징",
     '"advanced packaging" OR chiplet OR "heterogeneous integration" OR "through-silicon via" '
     'OR "2.5D integration" OR "3D integration" OR "wafer-level packaging" OR interposer'),
    ("semiconductor-display", "화합물 전력반도체",
     '("silicon carbide" OR "gallium nitride" OR "gallium oxide" OR "wide bandgap semiconductor") '
     'AND ("power device" OR "power electronics" OR transistor OR MOSFET OR HEMT OR converter)'),
    ("semiconductor-display", "차세대 고성능 센싱",
     '("image sensor" OR "MEMS sensor" OR biosensor OR "gas sensor" OR "intelligent sensor" '
     'OR "flexible sensor") AND (sensing OR detection OR device)'),
    ("semiconductor-display", "국방반도체",
     '("radiation-hardened" OR "defense electronics" OR "transmit receive module" '
     'OR "monolithic microwave integrated circuit") AND (semiconductor OR "integrated circuit" '
     'OR device OR chip)'),
    ("semiconductor-display", "반도체 소재·부품·장비",
     '(lithography OR "extreme ultraviolet" OR "atomic layer deposition" OR "plasma etching" '
     'OR "chemical mechanical polishing" OR photoresist) AND (semiconductor OR wafer '
     'OR "thin film")'),
    ("semiconductor-display", "무기발광 디스플레이",
     '("quantum dot light-emitting diode" OR "micro LED" OR "inorganic light-emitting diode" '
     'OR "perovskite light-emitting diode") AND (display OR "light-emitting")'),
    ("semiconductor-display", "차세대 OLED",
     '("organic light-emitting diode" OR OLED OR "thermally activated delayed fluorescence" '
     'OR "phosphorescent emitter" OR "flexible display substrate") AND (display OR emitter '
     'OR device)'),
    ("semiconductor-display", "디스플레이 소재·부품·장비",
     '(display OR panel) AND ("thin film transistor" OR encapsulation OR backplane '
     'OR "deposition process" OR "flexible substrate" OR "display manufacturing")'),

    ("bio", "합성생물학·바이오제조",
     '"synthetic biology" OR "metabolic engineering" OR "cell factory" OR biomanufacturing '
     'OR "genetic circuit" OR biofoundry OR "enzyme engineering"'),
    ("bio", "세포·유전자 치료",
     '"gene therapy" OR "cell therapy" OR "CAR-T" OR CRISPR OR "base editing" '
     'OR "prime editing" OR "stem cell therapy" OR "gene editing"'),
    ("bio", "차세대 백신",
     '(vaccine OR immunization) AND (mRNA OR "self-amplifying RNA" OR "circular RNA" '
     'OR "lipid nanoparticle" OR "vaccine platform" OR adjuvant OR "cancer vaccine")'),
    ("bio", "바이오 데이터·인공지능",
     '("machine learning" OR "deep learning" OR "artificial intelligence") AND (genomics '
     'OR proteomics OR "drug discovery" OR "clinical data" OR "protein structure prediction" '
     'OR biomarker)'),
    ("bio", "바이오 인공장기·혈액",
     '"artificial organ" OR organoid OR "tissue engineering" OR "artificial blood" '
     'OR "organ-on-a-chip" OR "3D bioprinting" OR xenotransplantation'),
    ("bio", "뇌-컴퓨터 인터페이스(BCI)",
     '"brain-computer interface" OR "brain-machine interface" OR "neural interface" '
     'OR neuroprosthetic OR electrocorticography OR "neural decoding"'),
    ("bio", "그린바이오",
     '"gene-edited crop" OR "plant genome editing" OR "crop improvement" OR "alternative protein" '
     'OR "cultured meat" OR "plant-based food" OR "molecular farming"'),

    ("battery", "리튬이온전지",
     '"lithium-ion battery" AND ("energy density" OR cathode OR anode OR electrolyte '
     'OR recycling OR "second life" OR "state of health" OR manufacturing)'),
    ("battery", "차세대 이차전지",
     '"solid-state battery" OR "lithium-sulfur battery" OR "sodium-ion battery" '
     'OR "lithium metal anode" OR "all-solid-state battery" OR "solid electrolyte"'),
    ("battery", "에너지저장시스템(ESS)",
     '("energy storage system" OR "grid-scale storage" OR "battery energy storage" '
     'OR "battery management system") AND (grid OR safety OR "thermal runaway" OR operation '
     'OR reliability)'),

    ("space-ocean", "재사용발사체",
     '"reusable launch vehicle" OR "rocket recovery" OR "vertical landing" OR "engine reignition" '
     'OR "reentry vehicle" OR "propulsive landing"'),
    ("space-ocean", "위성시스템·탑재체",
     'satellite AND (payload OR "onboard processing" OR "attitude control" OR "satellite bus" '
     'OR "remote sensing instrument" OR "constellation operation")'),
    ("space-ocean", "우주관측·탐사",
     '"space exploration" OR "planetary lander" OR "lunar rover" OR "deep space" '
     'OR "space observation" OR "planetary science mission" OR "space telescope" '
     'OR "asteroid mission"'),
    ("space-ocean", "첨단 항공 가스터빈 엔진·부품",
     '("gas turbine" OR turbofan OR "aero engine" OR "turbine blade" OR combustor) '
     'AND (aircraft OR aviation OR propulsion)'),
    ("space-ocean", "드론·도심항공교통(UAM)",
     '("unmanned aerial vehicle" OR drone OR "urban air mobility" OR "electric vertical takeoff" '
     'OR eVTOL OR "unmanned aircraft system") AND (control OR design OR operation OR traffic '
     'OR propulsion OR autonomy)'),
    ("space-ocean", "친환경·자율운항 선박",
     '(ship OR vessel OR maritime) AND ("autonomous navigation" OR "remote operation" '
     'OR "ammonia fuel" OR "hydrogen fuel" OR "electric propulsion" OR "carbon neutral fuel" '
     'OR "smart ship")'),

    ("materials", "혁신·지속가능 소재",
     '"critical raw material" OR "rare earth substitution" OR "sustainable material" '
     'OR "green chemistry" OR "material recycling" OR "bio-based material" '
     'OR "resource-efficient material"'),
    ("materials", "미래소재 및 설계·평가 플랫폼",
     '"materials informatics" OR "materials genome" OR "high-throughput screening of materials" '
     'OR "machine learning interatomic potential" OR "materials database" '
     'OR "computational materials design"'),

    ("energy-nuclear", "청정수소 생산·저장·운송·활용",
     '"green hydrogen" OR "water electrolysis" OR "hydrogen storage" OR "hydrogen carrier" '
     'OR "fuel cell" OR "ammonia cracking" OR "hydrogen production"'),
    ("energy-nuclear", "소형 모듈형 원자로(SMR)",
     '"small modular reactor" AND (nuclear OR reactor OR safety OR design)'),
    ("energy-nuclear", "선진원자력시스템 및 폐기물 관리",
     '"sodium-cooled fast reactor" OR "molten salt reactor" OR "high temperature gas-cooled reactor" '
     'OR "heat pipe reactor" OR "spent nuclear fuel" OR "radioactive waste disposal" '
     'OR "Generation IV reactor"'),
    ("energy-nuclear", "핵융합",
     '"nuclear fusion" OR tokamak OR "fusion reactor" OR "burning plasma" OR stellarator '
     'OR "fusion blanket" OR "magnetic confinement"'),
    ("energy-nuclear", "지능형 전력망",
     '("smart grid" OR "power system operation" OR "grid stability" OR "demand response" '
     'OR "grid-forming inverter" OR "power electronics converter") AND (grid OR "power system")'),
    ("energy-nuclear", "재생에너지",
     '(photovoltaic OR "solar cell" OR "wind turbine" OR "wind power" OR "geothermal energy" '
     'OR "renewable energy") AND (efficiency OR material OR system OR conversion OR integration)'),
    ("energy-nuclear", "탄소 포집·활용·저장(CCUS)",
     '("carbon capture" OR "CO2 utilization" OR "carbon storage" OR "direct air capture" '
     'OR "CO2 conversion" OR mineralization) AND (CO2 OR carbon)'),

    ("quantum", "양자컴퓨팅",
     '"quantum computing" OR "quantum computer" OR qubit OR "quantum algorithm" '
     'OR "quantum error correction" OR "superconducting qubit" OR "trapped ion"'),
    ("quantum", "양자통신",
     '"quantum communication" OR "quantum key distribution" OR "quantum network" '
     'OR "quantum repeater" OR "quantum teleportation" OR "entanglement distribution"'),
    ("quantum", "양자센싱",
     '("quantum sensing" OR "quantum sensor" OR "quantum metrology" OR "atomic clock" '
     'OR "nitrogen-vacancy center" OR "quantum imaging") AND (quantum OR sensing OR measurement)'),
]


def upgrade() -> None:
    conn = op.get_bind()
    # papers는 남긴다 — 검색 캐시라 새 검색식에서도 재사용된다.
    for table in (
        "analysis_runs", "analysis_papers", "analyses",
        "paper_extractions", "subfields", "fields",
    ):
        conn.execute(sa.text(f"DELETE FROM {table}"))

    fields = sa.table(
        "fields",
        sa.column("name", sa.String), sa.column("slug", sa.String),
        sa.column("order_no", sa.Integer),
    )
    op.bulk_insert(fields, [
        {"name": name, "slug": slug, "order_no": i}
        for i, (name, slug) in enumerate(FIELDS, start=1)
    ])

    ids = dict(conn.execute(sa.text("SELECT slug, id FROM fields")).all())
    subfields = sa.table(
        "subfields",
        sa.column("field_id", sa.Integer), sa.column("name", sa.String),
        sa.column("query", sa.Text), sa.column("query_kci", sa.Text),
        sa.column("active", sa.Boolean),
    )
    op.bulk_insert(subfields, [
        {"field_id": ids[slug], "name": name, "query": query,
         "query_kci": None, "active": True}
        for slug, name, query in SUBFIELDS
    ])


def downgrade() -> None:
    raise NotImplementedError("데이터 교체 마이그레이션이라 되돌릴 수 없다.")
