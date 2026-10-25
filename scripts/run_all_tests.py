"""
AlphaNiti v25.0 Institutional Test Suite Runner
Loads test modules directly from tests/ directory to avoid global namespace conflicts.
"""

import sys
import unittest
import importlib.util
from pathlib import Path

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

test_files = [
    "tests/test_pipeline_module1.py",
    "tests/test_dataset_splitter.py",
    "tests/test_tensor_builder.py",
    "tests/test_evidential_model.py",
    "tests/test_wasserstein_dro.py",
    "tests/test_portfolio_sizer.py",
    "tests/test_ledger.py",
    "tests/test_validation_and_backtest.py",
    "tests/test_safety_kernel_and_audit.py",
]

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for rel_path in test_files:
        file_path = PROJECT_ROOT / rel_path
        mod_name = file_path.stem
        spec = importlib.util.spec_from_file_location(mod_name, file_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(f"Total Tests Run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}, Errors: {len(result.errors)}")
    print(f"Overall Test Suite Status: {'PASSED' if result.wasSuccessful() else 'FAILED'}")
    print("=" * 60)

    sys.exit(0 if result.wasSuccessful() else 1)
