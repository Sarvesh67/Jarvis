// GENERATED from ontology.yaml by pipeline/gen_constraints.py — do not edit.

CREATE CONSTRAINT company_key IF NOT EXISTS FOR (n:Company) REQUIRE n.company_id IS UNIQUE;
CREATE INDEX company_norm_name IF NOT EXISTS FOR (n:Company) ON (n.norm_name);
CREATE INDEX company_trust_level IF NOT EXISTS FOR (n:Company) ON (n.trust_level);
CREATE INDEX company_pincode IF NOT EXISTS FOR (n:Company) ON (n.pincode);

CREATE CONSTRAINT facility_key IF NOT EXISTS FOR (n:Facility) REQUIRE n.facility_id IS UNIQUE;
CREATE INDEX facility_pincode IF NOT EXISTS FOR (n:Facility) ON (n.pincode);
CREATE INDEX facility_cluster_id IF NOT EXISTS FOR (n:Facility) ON (n.cluster_id);

CREATE CONSTRAINT licence_key IF NOT EXISTS FOR (n:Licence) REQUIRE (n.form_type, n.number, n.sla_state) IS UNIQUE;

CREATE CONSTRAINT ingredient_key IF NOT EXISTS FOR (n:Ingredient) REQUIRE n.inci_name IS UNIQUE;
CREATE INDEX ingredient_cas_primary IF NOT EXISTS FOR (n:Ingredient) ON (n.cas_primary);

CREATE CONSTRAINT productcategory_key IF NOT EXISTS FOR (n:ProductCategory) REQUIRE n.code IS UNIQUE;

CREATE CONSTRAINT cluster_key IF NOT EXISTS FOR (n:Cluster) REQUIRE n.cluster_id IS UNIQUE;

CREATE CONSTRAINT processcapability_key IF NOT EXISTS FOR (n:ProcessCapability) REQUIRE n.capability_id IS UNIQUE;

CREATE CONSTRAINT packagingcomponent_key IF NOT EXISTS FOR (n:PackagingComponent) REQUIRE n.component_id IS UNIQUE;

CREATE CONSTRAINT certification_key IF NOT EXISTS FOR (n:Certification) REQUIRE n.cert_id IS UNIQUE;

CREATE CONSTRAINT tradename_key IF NOT EXISTS FOR (n:TradeName) REQUIRE (n.name, n.supplier_id) IS UNIQUE;

CREATE CONSTRAINT regulatoryrule_key IF NOT EXISTS FOR (n:RegulatoryRule) REQUIRE n.rule_id IS UNIQUE;
CREATE INDEX regulatoryrule_jurisdiction IF NOT EXISTS FOR (n:RegulatoryRule) ON (n.jurisdiction);

CREATE CONSTRAINT product_key IF NOT EXISTS FOR (n:Product) REQUIRE n.product_id IS UNIQUE;

CREATE CONSTRAINT signal_key IF NOT EXISTS FOR (n:Signal) REQUIRE n.signal_id IS UNIQUE;

CREATE CONSTRAINT tradeflow_key IF NOT EXISTS FOR (n:TradeFlow) REQUIRE n.flow_id IS UNIQUE;

CREATE CONSTRAINT association_key IF NOT EXISTS FOR (n:Association) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT event_key IF NOT EXISTS FOR (n:Event) REQUIRE (n.name, n.edition, n.year) IS UNIQUE;

CREATE CONSTRAINT docchunk_key IF NOT EXISTS FOR (n:DocChunk) REQUIRE n.chunk_id IS UNIQUE;
CREATE VECTOR INDEX docchunk_embedding IF NOT EXISTS
  FOR (n:DocChunk) ON (n.embedding)
  OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};

