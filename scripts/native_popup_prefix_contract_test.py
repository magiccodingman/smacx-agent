"""Compile native label predicates and audit literal prefix lengths."""
from pathlib import Path
import re
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1] / 'bridge/src/agent_bridge.cpp').read_text()
for size, literal in re.findall(r'\.compare\(0,\s*(\d+),\s*"([^"]+)"\)', source):
    assert int(size) == len(literal), (size, literal)
function = re.search(r'bool bribe_demand_label\(.*?\n}', source, re.S).group()
information = re.search(r'bool reviewed_information_popup\(.*?\n}', source, re.S).group()
liberation = re.search(r'label\.compare\(0, sizeof\("LIBERATEBASE"\).*?== 0', source).group()
program = '#include <string>\n#include <cassert>\n' + function + information + '''
int main() {
 for (auto label : {"SPORESLAUNCHED", "SPOREFOREST"})
  assert(reviewed_information_popup(label));
 for (auto label : {"SPORESLAUNCHEDX", "SPORE", "SPOREFOREST0", "MONOLITH", "WEASELOUT"})
  assert(!reviewed_information_popup(label));
 for (auto label : {"DEMANDBRIBE0", "DEMANDBRIBE1", "DEMANDBRIBE12"})
  assert(bribe_demand_label(label));
 for (auto label : {"DEMANDTECH0", "DEMANDBRIB", "XDEMANDBRIBE0"})
  assert(!bribe_demand_label(label));
 for (std::string label : {"LIBERATEBASE", "LIBERATEBASE0", "LIBERATEBASE1"})
  assert(''' + liberation + ''');
}
'''
with tempfile.TemporaryDirectory() as temp:
    path = Path(temp)/'test.cpp'; path.write_text(program)
    subprocess.run(['g++', '-std=c++17', str(path), '-o',str(Path(temp)/'test')],check=True)
    subprocess.run([str(Path(temp)/'test')],check=True)
print('PASS: compiled demand/liberation labels and all literal prefix lengths')
