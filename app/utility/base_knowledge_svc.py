import copy
import os
import pickle
import shutil
import tarfile
import uuid

from datetime import datetime

import app.service.data_svc
from app.utility.base_service import BaseService
from app.objects.secondclass.c_fact import Fact, WILDCARD_STRING
from app.objects.secondclass.c_relationship import Relationship

DATA_BACKUP_DIR = app.service.data_svc.DATA_BACKUP_DIR
FACT_STORE_PATH = f"data{os.path.sep}fact_store"


class BaseKnowledgeService(BaseService):

    def __init__(self):
        self.log = self.create_logger('knowledge_svc')
        self.schema = dict(facts=[], relationships=[], rules=[], constraints=dict())
        self.fact_ram = copy.deepcopy(self.schema)

    # -- Fact API --
    async def _add_fact(self, fact, constraints=None):
        """
        Add a fact to the internal store
        :param fact: Fact to add
        :param constraints: any potential constraints
        """
        # facts can now be highly controlled, with visibility at the operation level, agent(s) level, or
        # custom groupings
        if not any(x == fact for x in self.fact_ram['facts']):
            fact._knowledge_id = uuid.uuid4()
            self.fact_ram['facts'].append(fact)
            if constraints:
                self.fact_ram['constraints'][fact._knowledge_id] = constraints

    async def _update_fact(self, criteria, updates):
        """
        Update a fact in the internal store
        :param criteria: dictionary containing fields to match on
        :param updates: dictionary containing fields to replace
        """
        matches = await self._get_facts(criteria)
        for match in matches:
            for k, v in updates.items():
                if hasattr(match, k):
                    setattr(match, k, v)

    async def _get_facts(self, criteria, restrictions=None):
        """
        Retrieve a fact from the internal store
        :param criteria: dictionary containing fields to match on
        :return: list of facts matching the criteria
        """
        complete_list = await self._locate('facts', query=criteria)
        return await self._apply_restrictions(complete_list, restrictions)

    async def _delete_fact(self, criteria):
        """
        Delete a fact from the internal store
        :param criteria: dictionary containing fields to match on
        """
        return await self._remove('facts', criteria)

    async def _get_meta_facts(self, meta_fact=None, agent=None, group=None):
        # Returns the complete set of facts associated with a meta-fact construct
        raise NotImplementedError

    async def _get_fact_origin(self, fact):
        """
        Identify the place where a fact originated, either the source that loaded it or its original link
        :param fact: Fact to get origin for (can be either a trait string or a full blown fact)
        :return: tuple - (String of either origin source id or origin link id, fact origin type)
        """
        workspace = copy.deepcopy(fact)
        if not getattr(workspace, 'links', False):
            fact_search = await self._get_facts(dict(trait=workspace))
            if fact_search:
                workspace = fact_search[0]
            else:
                return None, None

        if workspace.links:
            return str(workspace.links[0]), workspace.origin_type  # Return the id of the first link associated
        elif workspace.source:
            return str(workspace.source), workspace.origin_type  # Return the source of the fact if not found

        return None, None  # Default return value

    # -- Relationships API --

    async def _get_relationships(self, criteria, restrictions=None):
        """
        Retrieve relationships from the internal store
        :param criteria: dictionary containing fields to match on
        :return: list of matching relationships
        """
        complete_list = await self._locate('relationships', query=criteria)
        return await self._apply_restrictions(complete_list, restrictions)

    async def _add_relationship(self, relationship, constraints=None):
        """
        Add a relationship to the internal store
        :param relationship: Relationship object to add
        :param constraints: optional constraints on the use of the relationship
        """
        if not any((x.source == relationship.source) and (x.edge == relationship.edge) and
                   (x.target == relationship.target) and (x.origin == relationship.origin or not relationship.origin)
                   for x in self.fact_ram['relationships']):
            relationship._knowledge_id = uuid.uuid4()
            self.fact_ram['relationships'].append(relationship)
            if constraints:
                self.fact_ram['constraints'][relationship._knowledge_id] = constraints

    async def _update_relationship(self, criteria, updates):
        """
        Update a relationship in the internal store
        :param criteria: dictionary containing fields to match on
        :param updates: dictionary containing fields to modify
        """
        matches = await self._get_relationships(criteria)
        for match in matches:
            for k, v in updates.items():
                if getattr(match, k, "eMpTy") != "eMpTy":
                    if isinstance(getattr(match, k), Fact) and not isinstance(v, Fact):
                        handle = getattr(match, k)
                        for x in v.keys():
                            setattr(handle, x, v[x])
                    else:
                        setattr(match, k, v)

    async def _delete_relationship(self, criteria):
        """
        Remove a relationship from the internal store
        :param criteria: dictionary containing fields to match on
        """
        return await self._remove('relationships', criteria)

    # --- Rule API ---

    async def _add_rule(self, rule, constraints=None):
        """
        Add a rule to the internal store
        :param rule: Rule object to add
        :param constraints: dictionary containing fields to match on
        """
        ###
        #    rule.action: [DENY, ALLOW, EXCLUSIVE, EXCLUSIVE_TRAIT, EXCLUSIVE_VALUE], 'EXCLUSIVE_*' actions denote that
        #        the trait/value will be made mutually exclusive in its use to the agent/group/operation that is
        #        specified for. Essentially a fact is binded to mutex, and only one action can be using the fact
        #        at any one time.
        ###
        if not any((x.action == rule.action) and (x.trait == rule.trait) for x in self.fact_ram['rules']):
            rule._knowledge_id = uuid.uuid4()
            self.fact_ram['rules'].append(rule)
            if constraints:
                self.fact_ram['constraints'][rule._knowledge_id] = constraints

    async def _get_rules(self, criteria, restrictions=None):
        """
        Retrieve rules from the internal store
        :param criteria: dictionary containing fields to match on
        :return: list of matching rules
        """
        complete_list = [x for x in self.fact_ram['rules'] if await self._check_rule(x, criteria, True)]
        return await self._apply_restrictions(complete_list, restrictions)

    async def _delete_rule(self, criteria):
        """
        Remove a rule from the internal store
        :param criteria: dictionary containing fields to match on
        """
        sublist = [x for x in self.fact_ram['rules'] if await self._check_rule(x, criteria)]
        await self._clear_matching_constraints(sublist)
        self.fact_ram['rules'][:] = [x for x in self.fact_ram['rules'] if x not in sublist]
        return

    @staticmethod
    async def _check_rule(rule, desired_quals, wildcard=False):
        """
        Check whether or not a rule matches the provided criteria
        :param rule: Rule object to check
        :param desired_quals: dictionary containing fields to match on
        :param wildcard: whether or not to do wildcard matching on the 'match' field
        :return:
        """
        criteria_matches = []
        for k, v in desired_quals.items():
            if getattr(rule, k) == v:
                criteria_matches.append(True)
            elif k == 'match' and wildcard:  # support wildcard matching
                val = getattr(rule, k)
                local_stub = val.split('*')
                input_stub = v.split('*')
                if all(x in v for x in local_stub) or all(x in val for x in input_stub):
                    criteria_matches.append(True)
        if len(criteria_matches) == len(desired_quals):
            return True

    async def _locate(self, object_name, query=None):
        """
        Locate a matching object in the internal store
        :param object_name: object type
        :param query: dictionary of fields to match on
        :return: list of matching objects
        """
        try:
            return [obj for obj in self.fact_ram[object_name] if self._wildcard_match(obj, query)]
        except Exception as e:
            self.log.error('[!] LOCATE: %s' % e)

    async def _remove(self, object_name, query):
        """
        Remove objects from the internal store
        :param object_name: object type
        :param query: dictionary of fields to match on
        """
        try:
            sublist = [obj for obj in self.fact_ram[object_name] if self._wildcard_match(obj, query)]
            await self._clear_matching_constraints(sublist)
            self.fact_ram[object_name][:] = [obj for obj in self.fact_ram[object_name] if obj not in sublist]
        except Exception as e:
            self.log.error('[!] REMOVE: %s' % e)

    async def _clear_matching_constraints(self, objs):
        """
        Remove constraints associated with objects as part of deletion
        :param objs: list of objects being removed
        """
        for obj in objs:
            if obj._knowledge_id in self.fact_ram['constraints']:
                del self.fact_ram['constraints'][obj._knowledge_id]

    async def _get_matching_constraints(self, objs):
        """
        Identify matching constraints associated with objects
        :param objs: list of objects to get constraints for
        :return: list of relevant constraints
        """
        k_ids = [obj._knowledge_id for obj in objs]
        return [self.fact_ram['constraints'][constraint] for constraint in self.fact_ram['constraints']
                if constraint in k_ids]

    @staticmethod
    def _delete_file(path):
        if not os.path.exists(path):
            return
        elif os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

    @staticmethod
    def _destroy():
        """
        Reset the caldera data directory and server state.

        This creates a gzipped tarball backup of the data files tracked by caldera.
        Paths are preserved within the tarball, with all files having "data/" as the
        root.

        :return: None
        """
        if not os.path.exists(DATA_BACKUP_DIR):
            os.mkdir(DATA_BACKUP_DIR)

        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        tarball_path = os.path.join(DATA_BACKUP_DIR, f'backup-{timestamp}.tar.gz')

        with tarfile.open(tarball_path, 'w:gz') as tarball:
            if os.path.isfile(FACT_STORE_PATH):
                tarball.add(FACT_STORE_PATH)
                BaseKnowledgeService._delete_file(FACT_STORE_PATH)

    async def _save_state(self):
        """
        Saves the current internal store state to disk

        :return: None
        """
        await self.get_service('file_svc').save_file(FACT_STORE_PATH.split(os.path.sep)[1], pickle.dumps(self.fact_ram),
                                                     FACT_STORE_PATH.split(os.path.sep)[0])

    async def _restore_state(self):
        """
        Loads the internal store state from disk

        :return: None
        """
        if os.path.exists(FACT_STORE_PATH):
            _, store = await self.get_service('file_svc').read_file(FACT_STORE_PATH.split(os.path.sep)[1],
                                                                    FACT_STORE_PATH.split(os.path.sep)[0])
            # Pickle is only used to load a local file that caldera creates. Pickled data is not
            # received over the network.
            ram = pickle.loads(store)  # nosec
            for key in ram.keys():
                self.fact_ram[key] = []
                for c_object in ram[key]:
                    handle = self._load_wrapper(key)
                    constraints = []
                    if c_object._knowledge_id in ram[key]:
                        constraints = ram[key][c_object._knowledge_id]
                    await handle(c_object, constraints=constraints)
            self.log.debug('Restored data from persistent storage')

    def _load_wrapper(self, key):
        """
        Support wrapper to process different object types during load
        :param key: type of object to load
        :return: function handle to the correct loader function for the associated object type
        """
        if key == 'facts':
            return self._add_fact
        elif key == 'relationships':
            return self._add_relationship
        elif key == 'rules':
            return self._add_rule

    async def _apply_restrictions(self, complete_list, restrictions):
        """
        Apply restrictions to a list of objects (type agnostic)
        :param complete_list: List of objects to apply restrictions to
        :param restrictions: The restrictions to be applied (checked against the constraints internal store)
        :return: filtered list of input objects
        """
        def _check_restrictions(existing_limitations, desired_limitations):
            for restriction_name, restriction_value in desired_limitations:
                if restriction_name in existing_limitations:
                    if existing_limitations[restriction_name] != restriction_value:
                        return False
            return True

        if not restrictions:
            return complete_list
        ret = []
        for entry in complete_list:
            constraints = await self._get_matching_constraints([entry])
            if _check_restrictions(constraints, restrictions):
                ret.append(entry)
        return ret

    def _wildcard_match(self, obj, criteria):
        """
        Modified variant of the normal `match` method for objects that will return matches for wildcard fields
            [fact object].source
            [relationship object].origin
        :param obj: The object to validate
        :param criteria: The values to check against
        """
        if not criteria:
            return obj
        criteria_matches = []
        for k, v in criteria.items():
            if type(v) is tuple:
                for val in v:
                    if getattr(obj, k) == val:
                        criteria_matches.append(True)
            else:
                if getattr(obj, k) == v:
                    criteria_matches.append(True)
                elif isinstance(getattr(obj, k), Fact):  # this is a match based on a fact object in a relationship
                    if isinstance(v, Fact):
                        if getattr(obj, k) == v:
                            criteria_matches.append(True)
                    elif self._wildcard_match(getattr(obj, k), v):
                        criteria_matches.append(True)
                else:  # Wildcard match check
                    if (k == 'source' and isinstance(obj, Fact)) or (k == 'origin' and isinstance(obj, Relationship)):
                        if getattr(obj, k) == WILDCARD_STRING:
                            criteria_matches.append(True)
        if len(criteria_matches) >= len(criteria) and all(criteria_matches):
            return obj
