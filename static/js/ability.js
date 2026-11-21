/* eslint-disable */
function getAbilityParent(parentId) {
    return document.getElementById(parentId);
}

function getChildById(parent, childId) {
    return parent ? parent.querySelector(`#${childId}`) : null;
}

function resetSelect(selectElem, placeholderText) {
    if (!selectElem) return;
    selectElem.innerHTML = '';
    const option = document.createElement('option');
    option.disabled = true;
    option.selected = true;
    option.textContent = placeholderText;
    selectElem.appendChild(option);
}

function populateTechniques(parentId, abilities) {
    const parent = getAbilityParent(parentId);
    const techniqueFilter = getChildById(parent, 'ability-technique-filter');
    const tacticFilter = getChildById(parent, 'ability-tactic-filter');
    resetSelect(techniqueFilter, 'Choose a technique');

    const selectedTactic = tacticFilter?.selectedOptions?.[0];
    const tactic = selectedTactic?.dataset?.tactic;
    const found = [];
    abilities.forEach(function(ability) {
        if (ability.tactic.includes(tactic) && !found.includes(ability.technique_id)) {
            found.push(ability.technique_id);
            appendTechniqueToList(parentId, tactic, ability);
        }
    });
}

/**
 * Populate the abilities dropdown based on selected technique
 * @param {string} parentId - Parent ID used to search for dropdowns
 * @param {object[]} abilities - Abilities object array
 */
function populateAbilities(parentId, abilities) {
    const parent = getAbilityParent(parentId);
    const techniqueFilter = getChildById(parent, 'ability-technique-filter');
    const abilityFilter = getChildById(parent, 'ability-ability-filter');

    // Collect abilities matching technique
    const techniqueAbilities = [];
    const selectedTechnique = techniqueFilter?.selectedOptions?.[0];
    const attack_id = selectedTechnique?.dataset?.technique;
    abilities.forEach(function (ability) {
        if (ability.technique_id === attack_id) {
            techniqueAbilities.push(ability);
        }
    });

    // Clear, then populate the ability dropdown
    resetSelect(abilityFilter, `${techniqueAbilities.length} abilities`);
    techniqueAbilities.forEach(function (ability) {
        appendAbilityToList(parentId, ability);
    });
}

function appendTechniqueToList(parentId, tactic, value) {
    const parent = getAbilityParent(parentId);
    const techniqueFilter = getChildById(parent, 'ability-technique-filter');
    if (!techniqueFilter) return;

    const option = document.createElement('option');
    option.value = value.technique_id;
    option.dataset.technique = value.technique_id;
    option.textContent = `${value.technique_id} | ${value.technique_name}`;
    techniqueFilter.appendChild(option);
}

function appendAbilityToList(parentId, value) {
    const parent = getAbilityParent(parentId);
    const abilityFilter = getChildById(parent, 'ability-ability-filter');
    if (!abilityFilter) return;

    const option = document.createElement('option');
    option.value = value.name;
    option.textContent = value.name;
    option.abilityData = value;
    abilityFilter.appendChild(option);
}

function searchAbilities(parent, abilities){
    const parentElem = getAbilityParent(parent);
    const techniqueFilter = getChildById(parentElem, 'ability-technique-filter');
    const abilityFilter = getChildById(parentElem, 'ability-ability-filter');
    const searchFilter = getChildById(parentElem, 'ability-search-filter');
    resetSelect(techniqueFilter, 'All Techniques');
    if (abilityFilter) abilityFilter.innerHTML = '';

    const val = (searchFilter?.value || '').toLowerCase();
    const added = [];
    if (val) {
        abilities.forEach(function(ab) {
            let commandHasSearch = false;
            ab['executors'].forEach(function(executor) {
                if (executor['command'] != null && executor['command'].toLowerCase().includes(val)) {
                    commandHasSearch = true;
                }
            });

            let nameHasSearch = ab['name'].toLowerCase().includes(val);
            let descriptionHasSearch = ab['description'].toLowerCase().includes(val);
            if ((nameHasSearch || descriptionHasSearch || commandHasSearch) && !added.includes(ab['ability_id'])) {
                added.push(ab['ability_id']);
                appendAbilityToList(parent, ab);
            }
        });
    }
    if (abilityFilter) {
        const option = document.createElement('option');
        option.disabled = true;
        option.selected = true;
        option.textContent = `${added.length} abilities`;
        abilityFilter.prepend(option);
    }
}
