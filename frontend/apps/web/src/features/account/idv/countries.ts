/** Where the person lives. A label for the record: it never decides which documents are offered or whether the check can run. */
export const COUNTRIES: { id: string; label: string }[] = [
  ["AF", "Afghanistan"], ["AL", "Albania"], ["DZ", "Algeria"], ["AR", "Argentina"], ["AM", "Armenia"], ["AU", "Australia"], ["AT", "Austria"], ["AZ", "Azerbaijan"],
  ["BH", "Bahrain"], ["BD", "Bangladesh"], ["BY", "Belarus"], ["BE", "Belgium"], ["BJ", "Benin"], ["BO", "Bolivia"], ["BA", "Bosnia and Herzegovina"], ["BW", "Botswana"],
  ["BR", "Brazil"], ["BG", "Bulgaria"], ["KH", "Cambodia"], ["CM", "Cameroon"], ["CA", "Canada"], ["CL", "Chile"], ["CN", "China"], ["CO", "Colombia"], ["CR", "Costa Rica"],
  ["HR", "Croatia"], ["CY", "Cyprus"], ["CZ", "Czechia"], ["DK", "Denmark"], ["DO", "Dominican Republic"], ["EC", "Ecuador"], ["EG", "Egypt"], ["SV", "El Salvador"],
  ["EE", "Estonia"], ["ET", "Ethiopia"], ["FI", "Finland"], ["FR", "France"], ["GE", "Georgia"], ["DE", "Germany"], ["GH", "Ghana"], ["GR", "Greece"], ["GT", "Guatemala"],
  ["HK", "Hong Kong"], ["HU", "Hungary"], ["IS", "Iceland"], ["IN", "India"], ["ID", "Indonesia"], ["IQ", "Iraq"], ["IE", "Ireland"], ["IL", "Israel"], ["IT", "Italy"],
  ["JM", "Jamaica"], ["JP", "Japan"], ["JO", "Jordan"], ["KZ", "Kazakhstan"], ["KE", "Kenya"], ["KW", "Kuwait"], ["LV", "Latvia"], ["LB", "Lebanon"], ["LT", "Lithuania"],
  ["LU", "Luxembourg"], ["MY", "Malaysia"], ["MT", "Malta"], ["MU", "Mauritius"], ["MX", "Mexico"], ["MA", "Morocco"], ["MZ", "Mozambique"], ["NP", "Nepal"],
  ["NL", "Netherlands"], ["NZ", "New Zealand"], ["NG", "Nigeria"], ["NO", "Norway"], ["OM", "Oman"], ["PK", "Pakistan"], ["PA", "Panama"], ["PY", "Paraguay"], ["PE", "Peru"],
  ["PH", "Philippines"], ["PL", "Poland"], ["PT", "Portugal"], ["QA", "Qatar"], ["RO", "Romania"], ["SA", "Saudi Arabia"], ["SN", "Senegal"], ["RS", "Serbia"],
  ["SG", "Singapore"], ["SK", "Slovakia"], ["SI", "Slovenia"], ["ZA", "South Africa"], ["KR", "South Korea"], ["ES", "Spain"], ["LK", "Sri Lanka"], ["SE", "Sweden"],
  ["CH", "Switzerland"], ["TW", "Taiwan"], ["TZ", "Tanzania"], ["TH", "Thailand"], ["TN", "Tunisia"], ["TR", "Türkiye"], ["UG", "Uganda"], ["UA", "Ukraine"],
  ["AE", "United Arab Emirates"], ["GB", "United Kingdom"], ["US", "United States"], ["UY", "Uruguay"], ["UZ", "Uzbekistan"], ["VE", "Venezuela"], ["VN", "Vietnam"],
  ["ZM", "Zambia"], ["ZW", "Zimbabwe"],
].map(([id, label]) => ({ id: id as string, label: label as string }));
